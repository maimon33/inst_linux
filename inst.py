import os
import sys
import uuid
import urllib
import socket
import logging
import tempfile
import subprocess

import boto3
import click

from botocore.exceptions import ClientError, NoCredentialsError


session_id = uuid.uuid4().hex
DEFAULT_REGION = 'eu-west-1'
INSTANCE_DNS = ''

try:
    urllib2.urlopen('http://www.google.com', timeout=1)
    MY_IP = urllib.urlopen('http://whatismyip.org').read()
except (urllib2.URLError, socket.timeout):
    MY_IP = ""
    print "No Internet"
    sys.exit()

DISTRO_DICTIONARY = {
    'amazon':('ec2-user','ami-1a962263'),
    'redhat':('root','ami-bb9a6bc2'),
    'suse': ('root','ami-6fd16616'),
    'centos': ('centos','ami-192a9460'),
    'ubuntu': ('ubuntu','ami-8fd760f6')
    }


# Keypair prepiration
INST_KEYPAIR = open('{}/{}'.format(tempfile.gettempdir(), session_id), 'w+')
KEYPAIR_PATH = os.path.join(tempfile.gettempdir(), session_id)
os.chmod(KEYPAIR_PATH, 0600)


# Userdata for the AWS instance
USERDATA = ""
# USERDATA = """#!/bin/bash
# set -x
# sleep 10
# echo "#!/bin/bash\nif who | wc -l | grep -q 1 ; then shutdown -h +5 'Server Idle, Server termination' ; fi" > /root/inst_linux.sh
# chmod +x /root/inst_linux.sh
# echo "*/15 * * * * root /root/inst_linux.sh" >> /root/mycron
# crontab /root/mycron
# echo export TMOUT=300 >> /etc/environment"""


# Setting up a logger
logger = logging.getLogger('inst')
logger.setLevel(logging.INFO)
console = logging.StreamHandler()
logger.addHandler(console)

def _is_connected():
    try:
        socket.create_connection(("www.google.com", 80))
        return True
    except OSError:
        pass
    return False

def aws_client(resource=True, aws_service='ec2', region_name=DEFAULT_REGION):
    if resource:
        return boto3.resource(aws_service, region_name)
    else:
        return boto3.client(aws_service, region_name)

def distro_selection(distro):
    if distro in DISTRO_DICTIONARY:
        global INSTANCE_AMI
        INSTANCE_AMI = DISTRO_DICTIONARY[distro][1]
    else:
        logging.warning("{} is currently not supported".format(distro))
        sys.exit()
    
def keypair():
    keypair = aws_client(resource=False).create_key_pair(KeyName=session_id)
    INST_KEYPAIR.write(keypair['KeyMaterial'])
    INST_KEYPAIR.close()
    return session_id

def start_instance():
    try:
        client = aws_client()
    except NoCredentialsError as e:
        print "Yo2"
        if e.response['Error']['Code'] == 'Unable to locate credentials':
            print "Yo"
    instance = client.create_instances(
        ImageId=INSTANCE_AMI,
        MinCount=1,
        MaxCount=1,
        InstanceType='t2.nano',
        KeyName=keypair(),
        UserData=USERDATA,
        SecurityGroups=[create_security_group()],
        InstanceInitiatedShutdownBehavior='terminate')[0]
    logger.info('Waiting for instance to boot...')
    instance.wait_until_running()
    instance.load()
    global INSTANCE_DNS
    INSTANCE_DNS = instance.public_dns_name
    return instance.public_dns_name

def create_security_group():
    try:
        mysg = aws_client().create_security_group(
            GroupName="INST_LINUX", Description='Single serving SG')
        mysg.authorize_ingress(IpProtocol="tcp", CidrIp='0.0.0.0/0'.format(
            MY_IP), FromPort=22, ToPort=22)
    except ClientError as e:
        if e.response['Error']['Code'] == 'InvalidGroup.Duplicate':
            logger.debug("SG exists - Skipping")
            pass
    return "INST_LINUX"


CLICK_CONTEXT_SETTINGS = dict(
    help_option_names=['-h', '--help'],
    token_normalize_func=lambda param: param.lower(),
    ignore_unknown_options=True)

@click.command(context_settings=CLICK_CONTEXT_SETTINGS)
@click.argument('distro', default="ubuntu")
@click.option('-s',
              '--ssh',
              is_flag=True,
              help='Do you want to connect to your instance?')
@click.option('-v',
              '--verbose',
              is_flag=True,
              help="display run log in verbose mode")
@click.option('-d',
              '--debug',
              is_flag=True,
              help="debug new features")
def inst(distro, ssh, verbose, debug):
    """Get a Linux distro instance on AWS with one click
    """
    distro_selection(distro)

    if _is_connected():
        pass
    else:
        logger.warning("No Internet connection present!")
        sys.exit()
    
    if debug:
        print "Hi"
        find_ami()
        sys.exit()
    if verbose:
        logger.setLevel(logging.DEBUG)
    if ssh:
        ssh = subprocess.Popen(['ssh', '-i', KEYPAIR_PATH, '-o',
                                'StrictHostKeychecking=no',
                                '{}@{}'.format(
                                    DISTRO_DICTIONARY[distro][0],
                                    start_instance())], 
                                stderr=subprocess.PIPE)
        logger.info("ssh -i {} -o 'StrictHostKeychecking=no' {}@{}".format(
            KEYPAIR_PATH, DISTRO_DICTIONARY[distro][0], INSTANCE_DNS))
        if "Operation timed out" in ssh.stderr.readlines()[0]:
            logging.warning("Could not connect to Instance")
    else:
        start_instance()
        logger.info("In case connection fails connect manually\n")
        logger.info("ssh -i {} -o 'StrictHostKeychecking=no' {}@{}".format(
            KEYPAIR_PATH, DISTRO_DICTIONARY[distro][0], INSTANCE_DNS))
