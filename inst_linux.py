import os
import sys
import time
import uuid
import urllib

import boto3
import click

from botocore.exceptions import ClientError


session_id = uuid.uuid4().hex
DEFAULT_REGION = 'eu-west-1'
MY_IP = urllib.urlopen('http://whatismyip.org').read()


# Keypair prepiration
INST_KEYPAIR = open('/tmp/{}'.format(session_id), 'w+')
KEYPAIR_PATH = '/tmp/{}'.format(session_id)
os.chmod(KEYPAIR_PATH, 0600)


# Userdata for the AWS instance
USERDATA = """#!/bin/bash
echo export TMOUT=300 >> /etc/environment
echo "#!/bin/bash\nif who | wc -l | grep -q 1 ; then shutdown -h +5 'Server Idle, Server termination' ; fi" > /root/inst_linux.sh
chmod +x /root/inst_linux.sh
echo "* * * * * /root/inst_linux.sh" >> /root/mycron
crontab /root/mycron"""


class aws_client():


    def __init__(self):
        ACCESS_KEY = os.environ.get('AWS_ACCESS_KEY_ID')
        self.ACCESS_KEY = ACCESS_KEY

        SECRET_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY')
        self.SECRET_KEY = SECRET_KEY


    def _set_region(self):
        import subprocess
        iplist = self.get_regions()
        with open(os.devnull, "wb") as limbo:
            for ip in iplist:
                result=subprocess.Popen(["ping", "-c", "1", "-i", "0.1", "-n", "-W", "2", ip],
                                        stdout=limbo, stderr=limbo).wait()
                if result:
                    print ip, "inactive"
                else:
                    print ip, "active"

    def get_regions(self):
        regions_list = []
        response = self.aws_api(resource=False).describe_regions()['Regions']
        for region in response:
            regions_list.append(region['Endpoint'])
        return regions_list


    def aws_api(self, resource=True, aws_service='ec2'):
        if resource:
            return boto3.resource(aws_service,
                                  aws_access_key_id=self.ACCESS_KEY,
                                  aws_secret_access_key=self.SECRET_KEY,
                                  region_name=DEFAULT_REGION)
        else:
            return boto3.client(aws_service,
                                aws_access_key_id=self.ACCESS_KEY,
                                aws_secret_access_key=self.SECRET_KEY,
                                region_name=DEFAULT_REGION)


    def keypair(self):
        try:
            keypair = self.aws_api(resource=False).create_key_pair(KeyName=session_id)
            INST_KEYPAIR.write(keypair['KeyMaterial'])
            INST_KEYPAIR.close()
            return session_id
        except ClientError as e:
            if e.response['Error']['Code'] == 'InvalidKeyPair.Duplicate':
                print "Key exists - Skipping"
                return session_id


    def start_instance(self):
        instance = self.aws_api().create_instances(ImageId='ami-a8d2d7ce',
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
            mysg = self.aws_api().create_security_group(GroupName="INST_LINUX",Description='Single serving SG')
            mysg.authorize_ingress(IpProtocol="tcp",CidrIp='0.0.0.0/0'.format(MY_IP),FromPort=22,ToPort=22)
        except ClientError as e:
            if e.response['Error']['Code'] == 'InvalidGroup.Duplicate':
                print "SG exists - Skipping"
                pass
        return "INST_LINUX"





CLICK_CONTEXT_SETTINGS = dict(
    help_option_names=['-h', '--help'],
    token_normalize_func=lambda param: param.lower(),
    ignore_unknown_options=True)

@click.group(context_settings=CLICK_CONTEXT_SETTINGS)
@click.pass_context
def _inst_linux(ctx):
    """Get a Linux distro instance on AWS with one click
    """
    if os.environ.get('AWS_ACCESS_KEY_ID') and os.environ.get(
            'AWS_SECRET_ACCESS_KEY'):
        ctx.obj = {}
        ctx.obj['client'] = aws_client()
    else:
        print 'AWS credentials missing'
        # Kill process, AWS credentials are missing. no point moving forward!
        sys.exit()


@_inst_linux.command('start')
@click.option('-s',
              '--ssh',
              is_flag=True,
              help='Do you want to connect to your instance?')

def start(ssh):
    """List S3 content
    """
    client = aws_client()
    if ssh:
        os.system('ssh -i {} ubuntu@{}'.format(KEYPAIR_PATH, client.start_instance()))
    else:
        client.start_instance()

@_inst_linux.command('test')
def test():
    client = aws_client()
    client._set_region()
