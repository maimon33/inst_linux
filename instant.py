# TODO: See distro to implement a logger instead of printing.
# NO PRINTS! EVER!

import os
import re
import uuid
import urllib
import subprocess

import boto3
import click

from botocore.exceptions import ClientError


session_id = uuid.uuid4().hex
DEFAULT_REGION = 'eu-west-1'
INSTANCE_AMI = 'ami-a8d2d7ce'
MY_IP = urllib.urlopen('http://whatismyip.org').read()


# Keypair prepiration
# TODO: use `tempfile.gettmpdir()` or whatever to get the tmp dir.
# TODO: use `os.path.join` instead of concatenating the path.
# TODO: Don't open it here and close it in a function. What if the
# function fails? The file handler will be kept opened.
INST_KEYPAIR = open('/tmp/{}'.format(session_id), 'w+')
KEYPAIR_PATH = '/tmp/{}'.format(session_id)
os.chmod(KEYPAIR_PATH, 0600)


# Userdata for the AWS instance
USERDATA = """#!/bin/bash
set -x
sleep 10
echo "#!/bin/bash\nif who | wc -l | grep -q 1 ; then shutdown -h +5 'Server Idle, Server termination' ; fi" > /root/inst_linux.sh
chmod +x /root/inst_linux.sh
echo "*/15 * * * * root /root/inst_linux.sh" >> /root/mycron
crontab /root/mycron
echo export TMOUT=10 >> /etc/environment"""


class AWSClient(object):
    def __init__(self):
        self.access_key = os.environ.get('AWS_ACCESS_KEY_ID')
        self.secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')

    def _get_all_regions(self):
        region_list = []
        response = self.aws_client(
            resource=False).describe_regions()['Regions']
        for region in response:
            region_list.append(region['Endpoint'])
        return region_list

    def _get_best_region(self):
        # TODO: Separate to several functions
        ip_list = self._get_all_regions()
        region_response_time = {}
        for ip in ip_list:
            run_ping = subprocess.Popen(
                ["ping", "-c", "2", "-i", "0.1", "-n", "-W", "1", ip],
                stdout=subprocess.PIPE)
            ip = re.split('\.', ip)[1]
            ping_output = run_ping.stdout.read()
            for line in ping_output.splitlines():
                if "round-trip" in line:
                    ping_result = re.split('\s', line, 4)[3]
                    ping_avg = re.split('/', ping_result)[1]
                    region_response_time[ip] = float(ping_avg)
        return min(region_response_time, key=region_response_time.get)

    def aws_client(self, resource=True, aws_service='ec2'):
        # TODO: No need. Use env vars or aws config already default
        # in boto
        kwargs = dict(
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=DEFAULT_REGION
        )
        if resource:
            return boto3.resource(aws_service, **kwargs)
        else:
            return boto3.client(aws_service, **kwargs)

    def find_ami(self):
        # TODO: Improve search
        flavor = '*ubuntu*'
        image_count = 0
        ami = self.aws_client(resource=False).describe_images(Filters=[
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

    def keypair(self):
        try:
            keypair = self.aws_client(resource=False).create_key_pair(
                KeyName=session_id)
            INST_KEYPAIR.write(keypair['KeyMaterial'])
            INST_KEYPAIR.close()
            return session_id
        except ClientError as e:
            if e.response['Error']['Code'] == 'InvalidKeyPair.Duplicate':
                # TODO: NO PRINTING
                print "Key exists - Skipping"
                return session_id
            # TODO: else?

    def start_instance(self):
        client = self.aws_client()
        instance = client.create_instances(
            ImageId=INSTANCE_AMI,
            MinCount=1,
            MaxCount=1,
            InstanceType='t2.micro',
            KeyName=self.keypair(),
            UserData=USERDATA,
            SecurityGroups=[self.create_security_group()],
            InstanceInitiatedShutdownBehavior='terminate')[0]
        instance.wait_until_running()
        instance.load()
        return instance.public_dns_name

    def create_security_group(self):
        try:
            mysg = self.aws_client().create_security_group(
                GroupName="INST_LINUX", Description='Single serving SG')
            mysg.authorize_ingress(IpProtocol="tcp", CidrIp='0.0.0.0/0'.format(
                MY_IP), FromPort=22, ToPort=22)
        except ClientError as e:
            if e.response['Error']['Code'] == 'InvalidGroup.Duplicate':
                # TODO: no printing.
                print "SG exists - Skipping"
                pass
        return "INST_LINUX"


CLICK_CONTEXT_SETTINGS = dict(
    help_option_names=['-h', '--help'],
    token_normalize_func=lambda param: param.lower(),
    ignore_unknown_options=True)


@click.group(context_settings=CLICK_CONTEXT_SETTINGS)
@click.option('-s',
              '--ssh',
              is_flag=True,
              help='Do you want to connect to your instance?')
def instant(ssh):
    """Get a Linux distro instance on AWS with one click
    """
    client = AWSClient()
    # TODO: Handle error when instance creation failed.
    if ssh:
        # TODO: Replace os.system with subprocess.
        os.system(
            'ssh -i {} -o StrictHostKeychecking=no -o '
            'ServerAliveInterval=30 ubuntu@{}'.format(
                KEYPAIR_PATH, client.start_instance()))
    else:
        client.start_instance()
