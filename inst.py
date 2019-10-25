import os
import sys
import uuid
import time
import json
import base64
import logging
import tempfile
import subprocess

import boto3
import click

from requests import get
from colorlog import ColoredFormatter
from pkg_resources import resource_filename
from botocore.exceptions import ClientError, NoRegionError


session_id = uuid.uuid4().hex
MY_IP = get('https://api.ipify.org').text
DEFAULT_INSTANCE_TYPE = "t2.micro"
# DEFAULT_INSTANCE_TYPE = "c5.4xlarge"
DEFAULT_REGION = 'eu-west-1'
INSTANCE_AMI = 'ami-a8d2d7ce'

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
def setup_logger(verbose=False):
    """Return a logger with a default ColoredFormatter."""
    logging.addLevelName(21, 'SUCCESS')
    logging.addLevelName(22, 'PROCESS')
    formatter = ColoredFormatter(
        "%(log_color)s%(levelname)-8s%(reset)s - %(name)-5s -  %(message)s",
        datefmt=None,
        reset=True,
        log_colors={
            'ERROR':    'red',
            'CRITICAL': 'red',
            'INFO':     'cyan',
            'DEBUG':    'white',
            'SUCCESS':  'green',
            'PROCESS':  'purple',
            'WARNING':  'yellow',})

    logger = logging.getLogger('AWS-Inst')
    setattr(logger, 'success', lambda *args: logger.log(21, *args))
    setattr(logger, 'process', lambda *args: logger.log(22, *args))
    fh = logging.FileHandler('AWS-Inst.log')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logger.addHandler(fh)
    logger.addHandler(handler)
    if not verbose:
        logger.setLevel(logging.INFO)
    else:
        logger.setLevel(logging.DEBUG)
    return logger

def timer(sec):
    for i in tqdm(list(range(1, sec))):
        time.sleep(1)

def aws_client(resource=True, aws_service="ec2", region=DEFAULT_REGION):
    try:
        if resource:
            return boto3.resource(aws_service, region)
        else:
            return boto3.client(aws_service, region)
    except NoRegionError as e:
        logger.warning("Error reading 'Default Region'. Make sure boto is configured")
        sys.exit()

def get_region_name(region_code):
    default_region = 'EU (Ireland)'
    endpoint_file = resource_filename('botocore', 'data/endpoints.json')
    try:
        with open(endpoint_file, 'r') as f:
            data = json.load(f)
        return data['partitions'][0]['regions'][region_code]['description']
    except IOError:
        return default_region

# Get current AWS price for an on-demand instance
def get_price(region, instance, os):
    FLT = '[{{"Field": "tenancy", "Value": "shared", "Type": "TERM_MATCH"}},'\
      '{{"Field": "operatingSystem", "Value": "{o}", "Type": "TERM_MATCH"}},'\
      '{{"Field": "preInstalledSw", "Value": "NA", "Type": "TERM_MATCH"}},'\
      '{{"Field": "instanceType", "Value": "{t}", "Type": "TERM_MATCH"}},'\
      '{{"Field": "location", "Value": "{r}", "Type": "TERM_MATCH"}}]'

    f = FLT.format(r=region, t=instance, o=os)
    data = aws_client(
        resource=False, 
        aws_service='pricing', 
        region='us-east-1').get_products(
            ServiceCode='AmazonEC2', Filters=json.loads(f))
    od = json.loads(data['PriceList'][0])['terms']['OnDemand']
    id1 = list(od)[0]
    id2 = list(od[id1]['priceDimensions'])[0]
    return od[id1]['priceDimensions'][id2]['pricePerUnit']['USD']
    
def get_spot_info(spotid):
    client = aws_client(resource=False)
    spot_status = client.describe_spot_instance_requests(SpotInstanceRequestIds=[spotid])
    return spot_status["SpotInstanceRequests"][0]

def get_spot_price(type):
    client = aws_client(resource=False)
    return client.describe_spot_price_history(InstanceTypes=[type],
                                              MaxResults=1,
                                              ProductDescriptions=['Linux/UNIX (Amazon VPC)'])["SpotPriceHistory"][0]["SpotPrice"]        
        
def check_spot_status(client, SpotId, logger):
    status_code = get_spot_info(SpotId)["Status"]["Code"]
    while status_code != "fulfilled":
        status_code = get_spot_info(SpotId)["Status"]["Code"]
        status_msg = get_spot_info(SpotId)["Status"]["Message"]
        if status_code == 'capacity-not-available' or status_code == 'pending-fulfillment' or status_code == 'fulfilled':
            logger.info('{0}...'.format(status_code))
            time.sleep(1)
        else:
            logger.error("{0}\n{1}".format(status_code, status_msg))
            logger.error("cancel spot request- {0}".format(SpotId))
            client.cancel_spot_instance_requests(SpotInstanceRequestIds=[SpotId])
            sys.exit(0)

def create_security_group(logger, spot=False):
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
            groupId =  response['SecurityGroups'][0]['GroupId']
    if spot:
        return groupId
    else:
        return "INST_LINUX"

def keypair():
    keypair = aws_client(resource=False).create_key_pair(KeyName=session_id)
    INST_KEYPAIR.write(keypair['KeyMaterial'])
    INST_KEYPAIR.close()
    return session_id

def start_instance(logger, spot=False):
    if spot:
        client = aws_client(resource=False)
        LaunchSpecifications = {
            "ImageId": INSTANCE_AMI,
            "InstanceType": DEFAULT_INSTANCE_TYPE,
            "KeyName": keypair(),
            "UserData": base64.b64encode(USERDATA.encode("ascii")).\
                decode('ascii'),
            "SecurityGroupIds": [create_security_group(logger, spot=True)],
            ""
            "Placement": {"AvailabilityZone": ""}
        }
        spot_offer_price = get_spot_price(DEFAULT_INSTANCE_TYPE)
        spot_instance = client.request_spot_instances(
            SpotPrice=spot_offer_price,
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
            SecurityGroups=[create_security_group(logger)],
            InstanceInitiatedShutdownBehavior='terminate')[0]
    
    if not spot:
        instance_cost = get_price(get_region_name(DEFAULT_REGION), DEFAULT_INSTANCE_TYPE, 'Linux')
        logger.info("On demand instance will cost ${} per hour".format(instance_cost))
        logger.info('Waiting for instance to boot...')
    else:
        logger.info("Spot instance will cost ${} per hour".format(spot_offer_price))
        check_spot_status(client, SpotId, logger)
        aws_client(resource=False).create_tags(Resources=[SpotId], Tags=[{'Key': 'Name', 'Value': 'inst-assi'}])
        instance = aws_client().Instance(id=get_spot_info(SpotId)["InstanceId"])
    
    instance.wait_until_running()
    instance.load()
    instance.create_tags(Tags=[{'Key': 'Name', 'Value': 'inst-assi'}])
    
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

    logger = setup_logger(verbose=verbose)

    if spot:
        logger.info('starting spot instance...')
        instance_address = start_instance(logger, spot=True)
    else:
        logger.info('starting on-demand instance...')
        instance_address = start_instance(logger)

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
        
