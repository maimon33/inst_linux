import os
import re
import uuid
import urllib
import logging
import tempfile
import subprocess

import boto3
import click

from botocore.exceptions import ClientError


session_id = uuid.uuid4().hex
DEFAULT_REGION = 'eu-west-1'
INSTANCE_AMI = 'ami-a8d2d7ce'
MY_IP = urllib.urlopen('http://whatismyip.org').read()


# Keypair prepiration
# TODO: Don't open it here and close it in a function. What if the
# function fails? The file handler will be kept opened.
INST_KEYPAIR = open('{}/{}'.format(tempfile.gettempdir(), session_id), 'w+')
KEYPAIR_PATH = os.path.join(tempfile.gettempdir(), session_id)
os.chmod(KEYPAIR_PATH, 0600)


# Userdata for the AWS instance
USERDATA = """#!/bin/bash
set -x
sleep 10
echo "#!/bin/bash\nif who | wc -l | grep -q 1 ; then shutdown -h +5 'Server Idle, Server termination' ; fi" > /root/inst_linux.sh
chmod +x /root/inst_linux.sh
echo "*/15 * * * * root /root/inst_linux.sh" >> /root/mycron
crontab /root/mycron
echo export TMOUT=300 >> /etc/environment"""


# Setting up a logger
logger = logging.getLogger('inst')
logger.setLevel(logging.INFO)
console = logging.StreamHandler()
logger.addHandler(console)


def aws_client(resource=True, aws_service='ec2'):
    if resource:
        return boto3.resource(aws_service)
    else:
        return boto3.client(aws_service)


def _get_all_regions():
    region_list = []
    response = aws_client(
        resource=False).describe_regions()['Regions']
    for region in response:
        region_list.append(region['Endpoint'])
    return region_list


def ping_hosts(ips):
    ips_response_time = {}
    for ip in ips:
        execute_ping = subprocess.Popen(
            ["ping", "-c", "2", "-i", "0.1", "-n", "-W", "1", ip],
            stdout=subprocess.PIPE)
        ip = re.split('\.', ip)[1]
        ping_output = execute_ping.stdout.read()
        for line in ping_output.splitlines():
            if "round-trip" in line:
                ping_result = re.split('\s', line, 4)[3]
                ping_avg = re.split('/', ping_result)[1]
                ips_response_time[ip] = float(ping_avg)
    return ips_response_time


def _get_best_region():
    ip_list = _get_all_regions()
    regions_response_time = ping_hosts(ip_list)
    return min(regions_response_time, key=regions_response_time.get)


def find_ami():
    # TODO: Improve search
    flavor = '*ubuntu*'
    image_count = 0
    ami = aws_client(resource=False).describe_images(Filters=[
        {
            'Name': 'image-type',
            'Values': [
                'machine',
            ],
            'Name': 'name',
            'Values': [
                'bitnami*',
            ]
        },
    ])
    amis = ami['Images']
    for image in amis:
        try:
            if flavor in image['Name'] and image['ImageType'] == 'machine':
                image_count += 1
                return image['ImageId']
            else:
                pass
        except KeyError:
            continue
    # TODO: What happens when an AMI isn't found?


def keypair():
    keypair = aws_client(resource=False).create_key_pair(KeyName=session_id)
    INST_KEYPAIR.write(keypair['KeyMaterial'])
    INST_KEYPAIR.close()
    return session_id


def start_instance():
    client = aws_client()
    instance = client.create_instances(
        ImageId=INSTANCE_AMI,
        MinCount=1,
        MaxCount=1,
        InstanceType='t2.micro',
        KeyName=keypair(),
        UserData=USERDATA,
        SecurityGroups=[create_security_group()],
        InstanceInitiatedShutdownBehavior='terminate')[0]
    logger.info('Waiting for instance to boot...')
    instance.wait_until_running()
    instance.load()
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
@click.option('-s',
              '--ssh',
              is_flag=True,
              help='Do you want to connect to your instance?')
@click.option('-v',
              '--verbose',
              is_flag=True,
              help="display run log in verbose mode")
def inst(ssh, verbose):
    """Get a Linux distro instance on AWS with one click
    """
    # TODO: Handle error when instance creation failed.
    if verbose:
        logger.setLevel(logging.DEBUG)
    if ssh:
        ssh = subprocess.Popen(['ssh', '-i', KEYPAIR_PATH, '-o',
                                'StrictHostKeychecking=no',
                                'ubuntu@{}'.format(start_instance())],
                               stderr=subprocess.PIPE)
        if "Operation timed out" in ssh.stderr.readlines()[0]:
            logging.warning("Could not connect to Instance")
    else:
        print start_instance()