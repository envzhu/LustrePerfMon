# Copyright (c) 2017 DataDirect Networks, Inc.
# All Rights Reserved.
# Author: lixi@ddn.com
"""
Library for installing virtual machines
"""
# pylint: disable=too-many-lines
import sys
import logging
import traceback
import os
import shutil
import yaml
import filelock

# Local libs
from pyesmon import utils
from pyesmon import time_util
from pyesmon import esmon_common
from pyesmon import esmon_install_nodeps
from pyesmon import esmon_virt
from pyesmon import ssh_host
from pyesmon import watched_io
from pyesmon import lustre

ESMON_TEST_LOG_DIR = "/var/log/esmon_test"
ESMON_TEST_CONFIG_FNAME = "esmon_test.conf"
ESMON_TEST_CONFIG = "/etc/" + ESMON_TEST_CONFIG_FNAME


def esmon_do_test_install(workspace, install_server, mnt_path):
    """
    Run the install test
    """
    # pylint: disable=too-many-return-statements
    command = ("rpm -e esmon")
    retval = install_server.sh_run(command)
    if retval.cr_exit_status:
        logging.debug("failed to run command [%s] on host [%s], "
                      "ret = [%d], stdout = [%s], stderr = [%s]",
                      command,
                      install_server.sh_hostname,
                      retval.cr_exit_status,
                      retval.cr_stdout,
                      retval.cr_stderr)

    command = ("rpm -ivh %s/RPMS/rhel7/esmon-*.el7.x86_64.rpm" %
               (mnt_path))
    retval = install_server.sh_run(command)
    if retval.cr_exit_status:
        logging.error("failed to run command [%s] on host [%s], "
                      "ret = [%d], stdout = [%s], stderr = [%s]",
                      command,
                      install_server.sh_hostname,
                      retval.cr_exit_status,
                      retval.cr_stdout,
                      retval.cr_stderr)
        return -1

    install_config_fpath = (workspace + "/" +
                            esmon_common.ESMON_INSTALL_CONFIG_FNAME)
    ret = install_server.sh_send_file(install_config_fpath, "/etc")
    if ret:
        logging.error("failed to send file [%s] on local host to "
                      "directory [/etc] on host [%s]",
                      install_config_fpath, install_server.sh_hostname)
        return -1

    args = {}
    args["hostname"] = install_server.sh_hostname
    stdout_file = (workspace + "/" + "esmon_install.stdout")
    stderr_file = (workspace + "/" + "esmon_install.stderr")
    stdout_fd = watched_io.watched_io_open(stdout_file,
                                           watched_io.log_watcher_debug, args)
    stderr_fd = watched_io.watched_io_open(stderr_file,
                                           watched_io.log_watcher_debug, args)
    command = "esmon_install"
    retval = install_server.sh_run(command, stdout_tee=stdout_fd,
                                   stderr_tee=stderr_fd, return_stdout=False,
                                   return_stderr=False, timeout=None, flush_tee=True)
    stdout_fd.close()
    stderr_fd.close()

    if retval.cr_exit_status:
        logging.error("failed to run command [%s] on host [%s], "
                      "ret = [%d]",
                      command,
                      install_server.sh_hostname,
                      retval.cr_exit_status)
        return -1
    return 0


def esmon_test_install(workspace, install_server, host_iso_path):
    """
    Run the install test
    """
    # pylint: disable=bare-except
    mnt_path = "/mnt/" + utils.random_word(8)

    command = ("mkdir -p %s && mount -o loop %s %s" %
               (mnt_path, host_iso_path, mnt_path))
    retval = install_server.sh_run(command)
    if retval.cr_exit_status:
        logging.error("failed to run command [%s] on host [%s], "
                      "ret = [%d], stdout = [%s], stderr = [%s]",
                      command,
                      install_server.sh_hostname,
                      retval.cr_exit_status,
                      retval.cr_stdout,
                      retval.cr_stderr)
        return -1

    try:
        ret = esmon_do_test_install(workspace, install_server, mnt_path)
    except:
        ret = -1
        logging.error("exception: %s", traceback.format_exc())

    command = ("umount %s" % (mnt_path))
    retval = install_server.sh_run(command)
    if retval.cr_exit_status:
        logging.error("failed to run command [%s] on host [%s], "
                      "ret = [%d], stdout = [%s], stderr = [%s]",
                      command,
                      install_server.sh_hostname,
                      retval.cr_exit_status,
                      retval.cr_stdout,
                      retval.cr_stderr)
        ret = -1

    command = ("rmdir %s" % (mnt_path))
    retval = install_server.sh_run(command)
    if retval.cr_exit_status:
        logging.error("failed to run command [%s] on host [%s], "
                      "ret = [%d], stdout = [%s], stderr = [%s]",
                      command,
                      install_server.sh_hostname,
                      retval.cr_exit_status,
                      retval.cr_stdout,
                      retval.cr_stderr)
        return -1
    return ret


def esmon_test_lustre(workspace, hosts, config, config_fpath, install_config,
                      install_config_fpath):
    """
    Run Lustre tests
    """
    # pylint: disable=too-many-branches,too-many-return-statements
    # pylint: disable=too-many-statements,too-many-locals,too-many-arguments
    # pylint: disable=unused-variable
    lustre_rpm_dir = esmon_common.config_value(config, esmon_common.CSTR_LUSTRE_RPM_DIR)
    if lustre_rpm_dir is None:
        logging.error("no [%s] is configured, please correct file [%s]",
                      esmon_common.CSTR_LUSTRE_RPM_DIR, config_fpath)
        return -1

    e2fsprogs_rpm_dir = esmon_common.config_value(config, esmon_common.CSTR_E2FSPROGS_RPM_DIR)
    if e2fsprogs_rpm_dir is None:
        logging.error("no [%s] is configured, please correct file [%s]",
                      esmon_common.CSTR_E2FSPROGS_RPM_DIR, config_fpath)
        return -1

    lustre_rpms = lustre.LustreRPMs(lustre_rpm_dir)
    ret = lustre_rpms.lr_prepare()
    if ret:
        logging.error("failed to prepare Lustre RPMs")
        return -1

    lustre_configs = esmon_common.config_value(config, esmon_common.CSTR_LUSTRES)
    if lustre_configs is None:
        logging.error("no [%s] is configured, please correct file [%s]",
                      esmon_common.CSTR_LUSTRES, config_fpath)
        return -1

    for lustre_config in lustre_configs:
        # Parse general configs of Lustre file system
        fsname = esmon_common.config_value(lustre_config, esmon_common.CSTR_FSNAME)
        if fsname is None:
            logging.error("no [%s] is configured, please correct file [%s]",
                          esmon_common.CSTR_FSNAME, config_fpath)
            return -1

        mgs_nid = esmon_common.config_value(lustre_config, esmon_common.CSTR_MGS_NID)
        if mgs_nid is None:
            logging.error("no [%s] is configured, please correct file [%s]",
                          esmon_common.CSTR_MGS_NID, config_fpath)
            return -1

        lazy_prepare = esmon_common.config_value(lustre_config, esmon_common.CSTR_LAZY_PREPARE)
        if lazy_prepare is None:
            lazy_prepare = False
            logging.info("no [%s] is configured for fs [%s], using default value false",
                         esmon_common.CSTR_LAZY_PREPARE, fsname)
            return -1

        lustre_fs = lustre.LustreFilesystem(fsname, mgs_nid)

        # Parse MDT configs
        mdt_configs = esmon_common.config_value(lustre_config, esmon_common.CSTR_MDTS)
        if mdt_configs is None:
            logging.error("no [%s] is configured, please correct file [%s]",
                          esmon_common.CSTR_MDTS, config_fpath)
            return -1

        lustre_hosts = {}
        for mdt_config in mdt_configs:
            mdt_index = esmon_common.config_value(mdt_config, esmon_common.CSTR_INDEX)
            if mdt_index is None:
                logging.error("no [%s] is configured, please correct file [%s]",
                              esmon_common.CSTR_INDEX, config_fpath)
                return -1

            host_id = esmon_common.config_value(mdt_config, esmon_common.CSTR_HOST_ID)
            if host_id is None:
                logging.error("no [%s] is configured, please correct file [%s]",
                              esmon_common.CSTR_HOST_ID, config_fpath)
                return -1

            if host_id not in hosts:
                logging.error("no host with [%s] is configured in hosts, "
                              "please correct file [%s]",
                              host_id, config_fpath)
                return -1

            device = esmon_common.config_value(mdt_config, esmon_common.CSTR_DEVICE)
            if device is None:
                logging.error("no [%s] is configured, please correct file [%s]",
                              esmon_common.CSTR_DEVICE, config_fpath)
                return -1

            is_mgs = esmon_common.config_value(mdt_config, esmon_common.CSTR_IS_MGS)
            if is_mgs is None:
                logging.error("no [%s] is configured, please correct file [%s]",
                              esmon_common.CSTR_IS_MGS, config_fpath)
                return -1

            host = hosts[host_id]
            if host_id not in lustre_hosts:
                lustre_hosts[host_id] = host

            lustre.LustreMDT(lustre_fs, mdt_index, host, device,
                             is_mgs=is_mgs)

        # Parse OST configs
        ost_configs = esmon_common.config_value(lustre_config, esmon_common.CSTR_OSTS)
        if ost_configs is None:
            logging.error("no [%s] is configured, please correct file [%s]",
                          esmon_common.CSTR_OSTS, config_fpath)
            return -1

        for ost_config in ost_configs:
            ost_index = esmon_common.config_value(ost_config, esmon_common.CSTR_INDEX)
            if ost_index is None:
                logging.error("no [%s] is configured, please correct file [%s]",
                              esmon_common.CSTR_INDEX, config_fpath)
                return -1

            host_id = esmon_common.config_value(ost_config, esmon_common.CSTR_HOST_ID)
            if host_id is None:
                logging.error("no [%s] is configured, please correct file [%s]",
                              esmon_common.CSTR_HOST_ID, config_fpath)
                return -1

            if host_id not in hosts:
                logging.error("no host with [%s] is configured in hosts, "
                              "please correct file [%s]",
                              host_id, config_fpath)
                return -1

            device = esmon_common.config_value(ost_config, esmon_common.CSTR_DEVICE)
            if device is None:
                logging.error("no [%s] is configured, please correct file [%s]",
                              esmon_common.CSTR_DEVICE, config_fpath)
                return -1

            host = hosts[host_id]
            if host_id not in lustre_hosts:
                lustre_hosts[host_id] = host

            lustre.LustreOST(lustre_fs, ost_index, host, device)

        # Parse client configs
        client_configs = esmon_common.config_value(lustre_config,
                                                   esmon_common.CSTR_CLIENTS)
        if client_configs is None:
            logging.error("no [%s] is configured, please correct file [%s]",
                          esmon_common.CSTR_CLIENTS, config_fpath)
            return -1

        for client_config in client_configs:
            host_id = esmon_common.config_value(client_config, esmon_common.CSTR_HOST_ID)
            if host_id is None:
                logging.error("no [%s] is configured, please correct file [%s]",
                              esmon_common.CSTR_HOST_ID, config_fpath)
                return -1

            if host_id not in hosts:
                logging.error("no host with [%s] is configured in hosts, "
                              "please correct file [%s]",
                              host_id, config_fpath)
                return -1

            mnt = esmon_common.config_value(client_config, esmon_common.CSTR_MNT)
            if mnt is None:
                logging.error("no [%s] is configured, please correct file [%s]",
                              esmon_common.CSTR_MNT, config_fpath)
                return -1

            host = hosts[host_id]
            if host_id not in lustre_hosts:
                lustre_hosts[host_id] = host

            lustre.LustreClient(lustre_fs, host, mnt)

        # Install RPMs on MDS, OSS and clients
        for host_id, host in lustre_hosts.iteritems():
            lustre_host = lustre.LustreServerHost(host.sh_hostname,
                                                  identity_file=host.sh_identity_file,
                                                  local=host.sh_local)
            logging.debug("trying to install Lustre RPMs on host [%s] with host_id [%s]",
                          lustre_host.sh_hostname, host_id)
            ret = lustre_host.lsh_lustre_prepare(workspace, lustre_rpms,
                                                 e2fsprogs_rpm_dir,
                                                 lazy_prepare=lazy_prepare)
            if ret:
                logging.error("failed to install Lustre RPMs on host [%s]",
                              lustre_host.sh_hostname)
                return -1

        ret = lustre_fs.lf_format()
        if ret:
            logging.error("failed to format file system [%s]",
                          lustre_fs.lf_fsname)
            return -1

        ret = lustre_fs.lf_mount()
        if ret:
            logging.error("failed to mount file system [%s]",
                          lustre_fs.lf_fsname)
            return -1

        ret, esmon_server, esmon_clients = \
            esmon_install_nodeps.esmon_install_parse_config(workspace,
                                                            install_config,
                                                            install_config_fpath)
        if ret:
            logging.error("failed to parse config [%s]", config_fpath)
            return -1

        for esmon_client in esmon_clients.values():
            ret = esmon_client.ec_collectd_send_config(True)
            if ret:
                logging.error("failed to send test config to esmon client on host [%s]",
                              esmon_client.ec_host.sh_hostname)
                return -1

            ret = esmon_client.ec_collectd_restart()
            if ret:
                logging.error("failed to start esmon client on host [%s]",
                              esmon_client.ec_host.sh_hostname)
                return -1

            ret = esmon_client.ec_collectd_config_test.cc_check()
            if ret:
                logging.error("Influxdb doesn't have expected datapoints from "
                              "host [%s]", esmon_client.ec_host.sh_hostname)
                return -1

            ret = esmon_client.ec_collectd_send_config(False)
            if ret:
                logging.error("failed to send final config to esmon client on host [%s]",
                              esmon_client.ec_host.sh_hostname)
                return -1

            ret = esmon_client.ec_collectd_restart()
            if ret:
                logging.error("failed to start esmon client on host [%s]",
                              esmon_client.ec_host.sh_hostname)
                return -1

        ret = lustre_fs.lf_umount()
        if ret:
            logging.error("failed to umount file system [%s]",
                          lustre_fs.lf_fsname)
            return -1
    return 0


def esmon_do_test(workspace, config, config_fpath):
    """
    Run the tests
    """
    # pylint: disable=too-many-return-statements,too-many-locals
    # pylint: disable=too-many-branches,too-many-statements
    esmon_virt_config_fpath = esmon_common.config_value(config, "esmon_virt")
    if esmon_virt_config_fpath is None:
        logging.error("no [esmon_virt] is configured, "
                      "please correct file [%s]", config_fpath)
        return -1

    ret = esmon_virt.esmon_virt(workspace, esmon_virt_config_fpath)
    if ret:
        logging.error("failed to install the virtual machines")
        return -1

    ssh_host_configs = esmon_common.config_value(config, esmon_common.CSTR_SSH_HOST)
    if ssh_host_configs is None:
        logging.error("can NOT find [%s] in the config file, "
                      "please correct file [%s]",
                      esmon_common.CSTR_SSH_HOST, config_fpath)
        return -1

    hosts = {}
    for host_config in ssh_host_configs:
        host_id = host_config["host_id"]
        if host_id is None:
            logging.error("can NOT find [host_id] in the config of a "
                          "SSH host, please correct file [%s]",
                          config_fpath)
            return -1

        hostname = esmon_common.config_value(host_config, "hostname")
        if hostname is None:
            logging.error("can NOT find [hostname] in the config of SSH host "
                          "with ID [%s], please correct file [%s]",
                          host_id, config_fpath)
            return -1

        ssh_identity_file = esmon_common.config_value(host_config, "ssh_identity_file")

        if host_id in hosts:
            logging.error("multiple SSH hosts with the same ID [%s], please "
                          "correct file [%s]", host_id, config_fpath)
            return -1
        host = ssh_host.SSHHost(hostname, ssh_identity_file)
        hosts[host_id] = host

    install_server_hostid = esmon_common.config_value(config, "install_server")
    if install_server_hostid is None:
        logging.error("can NOT find [install_server] in the config file [%s], "
                      "please correct it", config_fpath)
        return -1

    if install_server_hostid not in hosts:
        logging.error("SSH host with ID [%s] is NOT configured in "
                      "[ssh_hosts], please correct file [%s]",
                      install_server_hostid, config_fpath)
        return -1
    install_server = hosts[install_server_hostid]

    server_host_config = esmon_common.config_value(config, esmon_common.CSTR_SERVER_HOST)
    if server_host_config is None:
        logging.error("can NOT find [%s] in the config file, "
                      "please correct file [%s]",
                      esmon_common.CSTR_SERVER_HOST,
                      config_fpath)
        return -1

    client_host_configs = esmon_common.config_value(config,
                                                    esmon_common.CSTR_CLIENT_HOSTS)
    if client_host_configs is None:
        logging.error("can NOT find [%s] in the config file, "
                      "please correct file [%s]",
                      esmon_common.CSTR_CLIENT_HOSTS,
                      config_fpath)
        return -1

    local_host = ssh_host.SSHHost("localhost", local=True)
    command = "ls esmon-*.iso"
    retval = local_host.sh_run(command)
    if retval.cr_exit_status:
        logging.error("failed to run command [%s] on host [%s], "
                      "ret = [%d], stdout = [%s], stderr = [%s]",
                      command,
                      local_host.sh_hostname,
                      retval.cr_exit_status,
                      retval.cr_stdout,
                      retval.cr_stderr)
        return -1

    current_dir = os.getcwd()
    iso_names = retval.cr_stdout.split()
    if len(iso_names) != 1:
        logging.error("found unexpected ISOs [%s] under currect directory [%s]",
                      iso_names, current_dir)
        return -1

    iso_name = iso_names[0]
    iso_path = current_dir + "/" + iso_name

    command = "mkdir -p %s" % workspace
    retval = install_server.sh_run(command)
    if retval.cr_exit_status:
        logging.error("failed to run command [%s] on host [%s], "
                      "ret = [%d], stdout = [%s], stderr = [%s]",
                      command,
                      install_server.sh_hostname,
                      retval.cr_exit_status,
                      retval.cr_stdout,
                      retval.cr_stderr)
        return -1

    ret = install_server.sh_send_file(iso_path, workspace)
    if ret:
        logging.error("failed to send ESMON ISO [%s] on local host to "
                      "directory [%s] on host [%s]",
                      iso_path, workspace,
                      install_server.sh_hostname)
        return -1

    host_iso_path = workspace + "/" + iso_name
    install_config = {}
    install_config[esmon_common.CSTR_ISO_PATH] = host_iso_path
    install_config[esmon_common.CSTR_SSH_HOST] = ssh_host_configs
    install_config[esmon_common.CSTR_CLIENT_HOSTS] = client_host_configs
    install_config[esmon_common.CSTR_SERVER_HOST] = server_host_config
    install_config_string = yaml.dump(install_config, default_flow_style=False)
    install_config_fpath = workspace + "/" + esmon_common.ESMON_INSTALL_CONFIG_FNAME
    with open(install_config_fpath, "wt") as install_config_file:
        install_config_file.write(install_config_string)

    ret = esmon_test_install(workspace, install_server, host_iso_path)
    if ret:
        return -1

    ret = esmon_test_lustre(workspace, hosts, config, config_fpath, install_config,
                            install_config_fpath)
    if ret:
        logging.error("failed to test Lustre")
        return -1

    return 0


def esmon_test_locked(workspace, config_fpath):
    """
    Start to test holding the confiure lock
    """
    # pylint: disable=too-many-branches,bare-except,too-many-locals
    # pylint: disable=too-many-statements
    save_fpath = workspace + "/" + ESMON_TEST_CONFIG_FNAME
    logging.debug("copying config file from [%s] to [%s]", config_fpath,
                  save_fpath)
    shutil.copyfile(config_fpath, save_fpath)

    config_fd = open(config_fpath)
    ret = 0
    try:
        config = yaml.load(config_fd)
    except:
        logging.error("not able to load [%s] as yaml file: %s", config_fpath,
                      traceback.format_exc())
        ret = -1
    config_fd.close()
    if ret:
        return -1

    try:
        ret = esmon_do_test(workspace, config, config_fpath)
    except:
        ret = -1
        logging.error("exception: %s", traceback.format_exc())

    return ret


def esmon_test(workspace, config_fpath):
    """
    Start to test
    """
    # pylint: disable=bare-except
    lock_file = config_fpath + ".lock"
    lock = filelock.FileLock(lock_file)
    try:
        with lock.acquire(timeout=0):
            try:
                ret = esmon_test_locked(workspace, config_fpath)
            except:
                ret = -1
                logging.error("exception: %s", traceback.format_exc())
            lock.release()
    except filelock.Timeout:
        ret = -1
        logging.error("someone else is holding lock of file [%s], aborting "
                      "to prevent conflicts", lock_file)
    return ret


def usage():
    """
    Print usage string
    """
    utils.eprint("Usage: %s <config_file>" %
                 sys.argv[0])


def main():
    """
    Test Exascaler monitoring
    """
    # pylint: disable=unused-variable
    reload(sys)
    sys.setdefaultencoding("utf-8")
    config_fpath = ESMON_TEST_CONFIG

    if len(sys.argv) == 2:
        config_fpath = sys.argv[1]
    elif len(sys.argv) > 2:
        usage()
        sys.exit(-1)

    identity = time_util.local_strftime(time_util.utcnow(), "%Y-%m-%d-%H_%M_%S")
    workspace = ESMON_TEST_LOG_DIR + "/" + identity

    if not os.path.exists(ESMON_TEST_LOG_DIR):
        os.mkdir(ESMON_TEST_LOG_DIR)
    elif not os.path.isdir(ESMON_TEST_LOG_DIR):
        logging.error("[%s] is not a directory", ESMON_TEST_LOG_DIR)
        sys.exit(-1)

    if not os.path.exists(workspace):
        os.mkdir(workspace)
    elif not os.path.isdir(workspace):
        logging.error("[%s] is not a directory", workspace)
        sys.exit(-1)

    print("Started testing ESMON using config [%s], "
          "please check [%s] for more log" %
          (config_fpath, workspace))
    utils.configure_logging(workspace)

    console_handler = utils.LOGGING_HANLDERS["console"]
    console_handler.setLevel(logging.DEBUG)

    ret = esmon_test(workspace, config_fpath)
    if ret:
        logging.error("test failed, please check [%s] for more log\n",
                      workspace)
        sys.exit(ret)
    logging.info("Passed the ESMON tests, please check [%s] "
                 "for more log", workspace)
    sys.exit(0)
