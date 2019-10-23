import os
import sys
import uuid
import base64
import logging
import tempfile
import subprocess

import boto3
import click

from requests import get
from botocore.exceptions import ClientError, NoRegionError


session_id = uuid.uuid4().hex
DEFAULT_INSTANCE_TYPE = "t2.micro"
DEFAULT_REGION = 'eu-west-1'
INSTANCE_AMI = 'ami-a8d2d7ce'
MY_IP = get('https://api.ipify.org').text

# Keypair prepiration
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
    try:
        if resource:
            return boto3.resource(aws_service)
        else:
            return boto3.client(aws_service)
    except NoRegionError as e:
        logger.warning("Error reading 'Default Region'. Make sure boto is configured")
        sys.exit()

def get_spot_info(spotid):
    client = aws_client(resource=False)
    spot_status = client.describe_spot_instance_requests(SpotInstanceRequestIds=[spotid])
    return spot_status["SpotInstanceRequests"][0]

def get_spot_price(type):
    client = aws_client(resource=False)
    return client.describe_spot_price_history(InstanceTypes=[type],MaxResults=1,ProductDescriptions=['Linux/UNIX (Amazon VPC)'])["SpotPriceHistory"][0]["SpotPrice"]        
        
def check_spot_status(client, SpotId):
    status_code = get_spot_info(SpotId)["Status"]["Code"]
    while status_code != "fulfilled":
        status_code = get_spot_info(SpotId)["Status"]["Code"]
        status_msg = get_spot_info(SpotId)["Status"]["Message"]
        if status_code == 'capacity-not-available' or status_code == 'pending-fulfillment' or status_code == 'fulfilled':
            sys.stdout.write('\x1b[1A')
            sys.stdout.write('\x1b[2K')
            print('{0}...'.format(status_code))
        else:
            print("{0}\n{1}".format(status_code, status_msg))
            print("cancel spot request- {0}".format(SpotId))
            client.cancel_spot_instance_requests(SpotInstanceRequestIds=[SpotId])
            sys.exit(0)

def create_security_group():
    try:
        My_SecurityGroup = aws_client().create_security_group(
            GroupName="INST_LINUX", Description='Single serving SG')
        My_SecurityGroup.authorize_ingress(IpProtocol="tcp", CidrIp='0.0.0.0/0'.format(
            MY_IP), FromPort=22, ToPort=22)
    except ClientError as e:
        if e.response['Error']['Code'] == 'InvalidGroup.Duplicate':
            logger.debug("SG exist")
            response = aws_client(resource=False).describe_security_groups(
                Filters=[
                    dict(Name='group-name', Values=["INST_LINUX"])
                ]
            )
            return response['SecurityGroups'][0]['GroupId']
    return

def keypair():
    keypair = aws_client(resource=False).create_key_pair(KeyName=session_id)
    INST_KEYPAIR.write(keypair['KeyMaterial'])
    INST_KEYPAIR.close()
    return session_id

def start_instance(spot=False):
    if spot:
        create_security_group()
        client = aws_client(resource=False)
        LaunchSpecifications = {
            "ImageId": INSTANCE_AMI,
            "InstanceType": DEFAULT_INSTANCE_TYPE,
            "KeyName": keypair(),
            "UserData": base64.b64encode(USERDATA.encode("ascii")).\
                decode('ascii'),
            "SecurityGroupIds": [create_security_group()],
            "Placement": {"AvailabilityZone": ""}
        }
        spot_instance = client.request_spot_instances(
            SpotPrice=get_spot_price(DEFAULT_INSTANCE_TYPE),
            Type="one-time",
            InstanceCount=1,
            LaunchSpecification=LaunchSpecifications)
        SpotId = spot_instance["SpotInstanceRequests"][0]["SpotInstanceRequestId"]
    else:
        client = aws_client()
        instance = client.create_instances(
            ImageId=INSTANCE_AMI,
            MinCount=1,
            MaxCount=1,
            InstanceType=DEFAULT_INSTANCE_TYPE,
            KeyName=keypair(),
            UserData=USERDATA,
            SecurityGroups=[create_security_group()],
            InstanceInitiatedShutdownBehavior='terminate')[0]
    
    if not spot:
        logger.info('Waiting for instance to boot...')
    else:
        check_spot_status(client, SpotId)
        instance = aws_client().Instance(id=get_spot_info(SpotId)["InstanceId"])
    
    instance.wait_until_running()
    instance.load()
    return instance.public_dns_name


CLICK_CONTEXT_SETTINGS = dict(
    help_option_names=['-h', '--help'],
    token_normalize_func=lambda param: param.lower(),
    ignore_unknown_options=True)


@click.command(context_settings=CLICK_CONTEXT_SETTINGS)
@click.option('-s',
              '--ssh',
              is_flag=True,
              help='Do you want to connect to your instance?')
@click.option('--spot',
              is_flag=True,
              help='Do you want a spot instance type?')
@click.option('-v',
              '--verbose',
              is_flag=True,
              help="display run log in verbose mode")
def inst(ssh, spot, verbose):
    """Get a Linux distro instance on AWS with one click
    """
    # TODO: Handle error when instance creation failed.
    if verbose:
        logger.setLevel(logging.DEBUG)

    if spot:
        instance_address = start_instance(spot=True)
    else:
        instance_address = start_instance()

    if ssh:
        ssh = subprocess.Popen(['ssh', '-i', KEYPAIR_PATH, '-o',
                                'StrictHostKeychecking=no',
                                'ubuntu@{}'.format(instance_address)],
                                stderr=subprocess.PIPE)
        if "Operation timed out" in ssh.stderr.readlines()[0]:
            logging.warning("Could not connect to Instance")
    else:
        print "To connect to your instance:"
        print "ssh -i {} ubuntu@{}".format(KEYPAIR_PATH, instance_address)
        
