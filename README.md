# inst_linux

Easily get a linux distro on AWS

**Prerequeset**<br>
Prior to using this tool you'll need to set boto config to grant access to your AWS account.<br>
Either by setting system variables (`AWS_ACCESS_KEY_ID` & `AWS_SECRET_ACCESS_KEY`) or boto.cfg file

### Usage

```$xslt
$ inst -s
Waiting for instance to boot...
Welcome to Ubuntu 16.04.2 LTS (GNU/Linux 4.4.0-1013-aws x86_64)

 * Documentation:  https://help.ubuntu.com
 * Management:     https://landscape.canonical.com
 * Support:        https://ubuntu.com/advantage

  Get cloud support with Ubuntu Advantage Cloud Guest:
    http://www.ubuntu.com/business/services/cloud

0 packages can be updated.
0 updates are security updates.



The programs included with the Ubuntu system are free software;
the exact distribution terms for each program are described in the
individual files in /usr/share/doc/*/copyright.

Ubuntu comes with ABSOLUTELY NO WARRANTY, to the extent permitted by
applicable law.

To run a command as administrator (user "root"), use "sudo <command>".
See "man sudo_root" for details.
```