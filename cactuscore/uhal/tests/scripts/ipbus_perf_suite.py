#!/bin/env python
"""
Usage: ipbus_perf_suite.py <config file>

This script runs the standard set of IPbus performance & robostness measurements.

N.B: You must set LD_LIBRARY_PATH and PATH appropriately before this 
     [So that this script can be run for checked-out sources (during dev) or installed RPMs.]
"""

from datetime import datetime
import fcntl
import getpass
from math import sqrt
from math import ceil
import numpy
import os
import paramiko
from random import randint
import re
import signal
from socket import gethostname
import subprocess
import sys
import tempfile
import threading
import time

####################################################################################################
#  GLOBAL OPTIONS

TEST_CMD_TIMEOUT_S = 60

UHAL_PC_NAME = gethostname()

CH_PC_NAME = 'pc-e1x06-36-01'
CH_PC_USER = 'tsw'
CH_PC_ENV  = {'PATH':os.environ['PATH'],
              'LD_LIBRARY_PATH':os.environ['LD_LIBRARY_PATH'] 
              }

CH_SYS_CONFIG_LOCATION = "/cactusbuild/trunk/cactuscore/controlhub/RPMBUILD/SOURCES/lib/controlhub/releases/2.2.0/sys.config"

TARGETS = ['amc-e1a12-19-09:50001',
           'amc-e1a12-19-10:50001',
           'amc-e1a12-19-04:50001']

DIRECT_UDP_NR_IN_FLIGHT = 16

CACTUS_REVISION = "SW @ r23785, FW @ r23770"

CHANGES_TAG = "No changes"


#####################################################################################################
# CONSTANTS derived from global options



###################################################################################################
#  SETUP LOGGING

import logging

SCRIPT_LOGGER = logging.getLogger("ipbus_perf_suite_log")

SCRIPT_LOG_HANDLER = logging.StreamHandler(sys.stdout)
SCRIPT_LOG_FORMATTER = logging.Formatter("%(asctime)-15s [0x%(thread)x] %(levelname)-7s > %(message)s")
SCRIPT_LOG_HANDLER.setFormatter(SCRIPT_LOG_FORMATTER)

SCRIPT_LOGGER.addHandler(SCRIPT_LOG_HANDLER)
SCRIPT_LOGGER.setLevel(logging.WARNING)

####################################################################################################
#  SETUP MATPLOTLIB

import matplotlib.pyplot as plt

####################################################################################################
#  EXCEPTION CLASSES

class CommandHardTimeout(Exception):
    '''Exception thrown when command spends too long running and has to be killed by the script'''
    def __init__(self, cmd, timeout, output):
        self.cmd = cmd
        self.timeout = timeout
        self.output = output
    def __str__(self):
        return "Command '" + self.cmd + "', timeout was " + str(self.timeout) + " seconds, and output so far was:\n" + output

class CommandBadExitCode(Exception):
    '''Exception thrown when command returns a non-zero exit code'''
    def __init__(self, cmd, exit_code, output):
        self.cmd = cmd
        self.value = exit_code
        self.output = output
    def __str__(self):
        return "Exit code " + str(self.value) + " from command '" + self.cmd + "'\nOutput was:\n" + self.output


####################################################################################################
#  COMMAND-RUNNING/PARSING FUNCTIONS

def run_command(cmd, ssh_client=None, parser=None):
  """
  Run command, returning tuple of exit code and stdout/err.
  The command will be killed if it takes longer than TEST_CMD_TIMEOUT_S
  """
  if (parser is None) and (cmd.startswith("PerfTester.exe") or cmd.startswith("perf_tester.escript")):
      parser = parse_perftester
  if cmd.startswith("sudo"):
      cmd = "sudo PATH=$PATH " + cmd[4:]

  if ssh_client is None:
    SCRIPT_LOGGER.debug("Running (locally): "+cmd)
    t0 = time.time()

    p  = subprocess.Popen(cmd,stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=None, shell=True, preexec_fn=os.setsid)
    try:
      f1 = fcntl.fcntl(p.stdout, fcntl.F_GETFL)
      fcntl.fcntl(p.stdout, fcntl.F_SETFL, f1 | os.O_NONBLOCK)

      stdout = ""
      last = time.time()

      while True:
          current = time.time()

          try:
              nextline = p.stdout.readline()
              if not nextline:
                  break

              last = time.time()
              stdout += nextline

          except IOError:
              time.sleep(0.1)
             
              if (current-t0) > TEST_CMD_TIMEOUT_S:
                  os.killpg(p.pid, signal.SIGTERM)
                  raise CommandHardTimeout(cmd, TEST_CMD_TIMEOUT_S, stdout)

    except KeyboardInterrupt:
        print "+ Ctrl-C detected."
        os.killpg(p.pid, signal.SIGTERM)
        raise KeyboardInterrupt

    exit_code = p.poll()
    if exit_code:
        raise CommandBadExitCode(cmd, exit_code, stdout)

    if parser in [None,False]:
        return exit_code, stdout
    else:
        return parser(stdout)

  else:
    for env_var, value in CH_PC_ENV.iteritems():
        cmd = "export " + env_var + "=" + value + " ; " + cmd
    SCRIPT_LOGGER.debug("Running (remotely): "+cmd)
    stdin, stdout, stderr = ssh_client.exec_command(cmd)
    exit_code = stdout.channel.recv_exit_status()
    output = "".join( stdout.readlines() ) + "".join( stderr.readlines() )
   
    SCRIPT_LOGGER.debug("Output is ...\n"+output)
 
    if exit_code: 
        raise CommandBadExitCode(cmd, exit_code, output)

    if parser in [None, False]:
        return exit_code, output
    else:
        return parser(output)


def parse_perftester(cmd_output):
    """Parses output of PerfTester.exe, and return tuple of latency per iteration (us), and bandwidth (Mb/s)"""
    
    m1 = re.search(r"^Test iteration frequency\s+=\s*([\d\.]+)\s*Hz", cmd_output, flags=re.MULTILINE)
    freq = float(m1.group(1))
    m2 = re.search(r"^Average \S+ bandwidth\s+=\s*([\d\.]+)\s*KB/s", cmd_output, flags=re.MULTILINE)
    bw = float(m2.group(1)) / 125e3

    SCRIPT_LOGGER.info("Parsed: Latency = " + str(1000000.0/freq) + "us/iteration , bandwidth = " + str(bw) + "Gb/s")
    return (1000000.0/freq, bw)


def run_ping(target, ssh_client=None):
    '''Runs unix ping command, parses output and returns average latency'''
    
    target_dns = target.split(":")[0]
    run_command("ping -c 2 " + target_dns, ssh_client)
    exit_code, output = run_command("ping -c 10 " + target_dns, ssh_client)

    m = re.search(r"^.+\s=\s[\d\.]+/([\d\.]+)/[\d\.]+/[\d\.]+\sms", output, flags=re.MULTILINE)
    evg_latency_us = 1000 * float(m.group(1))

    return avg_latency_us


def cpu_mem_usage(cmd_to_check, ssh_client=None):
    assert " " not in cmd_to_check

    output = run_command("top -b -n 1 | grep "+cmd_to_check, ssh_client=ssh_client)[1]
    cpu, mem = 0.0, 0.0    

    regex = re.compile("^\\s*\\d+\\s+\\w+\\s+\\S+\\s+\\S+\\s+"        # PID USER      PR  NI  
                       "\\w+\\s+\\w+\\s+\\w+\\s+\\S+\\s+"         # VIRT  RES  SHR S
                       "([\\d+\\.]+)\\s+([\\d+\\.]+)\\s+"         # %CPU %MEM
                       "[\\d:\\.]+\\s+" + re.escape(cmd_to_check) # TIME+  COMMAND
                       )

    for line in output.splitlines():
        m = regex.search(line)
        if m:
            cpu += float(m.group(1))
            mem += float(m.group(2))

    return cpu, mem


def start_controlhub(ssh_client=None):
    run_command("sudo controlhub_start", ssh_client=ssh_client)


def stop_controlhub(ssh_client=None):
    run_command("sudo controlhub_stop", ssh_client=ssh_client)
    

class CommandRunner:
    """
    Class for running a set of commands in parallel - each in their own thread - whilst 
    monitoring CPU & mem usage of some other commands in the main thread.
    """
    def __init__(self, monitoring_options):
        """
        Contructor. 
        The monitoring_options argument must be a list of (command_to_montor, ssh_client) tuples
        """
        self.monitor_opts = monitoring_options

    def _run_in_thread(self, cmd, ssh_client, index):
        SCRIPT_LOGGER.debug('CommandRunner thread starting for command "' + cmd + '"')
        try:
            retval = run_command(cmd, ssh_client)
            self._cmd_completed = True
            self.cmd_results[index] = retval
        except Exception as e:
            SCRIPT_LOGGER.exception('Exception of type "' + str(type(e)) + '" thrown when executing the command "' + cmd + '" in this thread.')
            self._cmd_completed = True
            self.cmd_results[index] = e 

    def run(self, cmds):
        """
        Runs the commands via ssh_client simultaneously in different threads, blocking until they are all finished.
        The argument cmds is a list of commands, or (cmd, ssh_client) tuples. If ssh_client is not specified, then command is run locally.
        Returns a 2-tuple - element 1 is list of run_command(cmd) return values; element 2 is a list of (cmd, av_cpu, av_mem) tuples.
        """
        assert len(cmds)>0        
        SCRIPT_LOGGER.info( "CommandRunner will now run the following commands simultaneously:\n     " + "\n     ".join(cmds) )

        self.cmd_results = [None for x in cmds]
        self.threads = []
        self._cmd_completed = False

        monitor_results = []
        for cmd, ssh_client in self.monitor_opts:
            monitor_results.append( (cmd, ssh_client, [], []) )

        # Set each command running
        for i in range(len(cmds)):
            if isinstance(cmds[i], basestring):
                cmd = cmds[i]
                ssh_client = None
            else:
                cmd, ssh_client = cmds[i]
            t = threading.Thread(target=self._run_in_thread, args=(cmd, ssh_client, i) )
            self.threads.append(t)

        for t in self.threads:
            t.start()

        # Monitor CPU/mem usage whilst *all* commands running (i.e. until any one of the commands exits)
        time.sleep(0.4)
        SCRIPT_LOGGER.debug('CommandRunner is now starting monitoring.')
        while not self._cmd_completed:
            try:
                for cmd, ssh_client, cpu_vals, mem_vals in monitor_results:
                    meas_cpu, meas_mem = cpu_mem_usage(cmd, ssh_client)
#                    print meas_cpu, meas_mem
                    cpu_vals.append(meas_cpu)
                    mem_vals.append(meas_mem)
            except CommandBadExitCode as e:
                if not self._cmd_completed:
                    raise
            time.sleep(0.05) 
        cpu_vals.pop()
        mem_vals.pop() 

        # Wait (w/o monitoring)
        SCRIPT_LOGGER.debug('One of the commands has now finished. No more monitoring - just wait for rest to finish.')
        for t in self.threads:
            t.join()
            SCRIPT_LOGGER.debug("Thread with ID 0x%s has now finished. It's being removed from the list of running threads." % hex(t.ident))
            del t

        # Check for async exceptions
        for result in self.cmd_results:
            if issubclass(type(result), Exception):
                SCRIPT_LOGGER.error("An exception was raised in one of CommandRunner's command-running threads. Re-raising now ...")
                raise result

        return [(cmd, numpy.mean(cpu_vals), numpy.mean(mem_vals)) for cmd, ssh_client, cpu_vals, mem_vals in monitor_results], self.cmd_results


####################################################################################################
# SSH / SFTP FUNCTIONS

def ssh_into(hostname, username):
    try:
        passwd = getpass.getpass('Password for ' + username + '@' + hostname + ': ')
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(hostname, username=username, password=passwd)
        print " SSH client now connected!"
        return client
    except paramiko.AuthenticationException, e:
        print "Authenication exception caught. Details:", str(e)
        print "Let's try again ...\n"
        return ssh_into(hostname, username)


def update_controlhub_sys_config(max_in_flight, ssh_client, sys_config_location):
    """Writes new ControlHub sys.config file in /tmp, and copies to remote PC via SFTP."""
    
    content = ('[\n'
               '%% write log files to a particular location\n'
               '  {sasl,\n'
               '    [\n'
               '      {sasl_error_logger, {file, "/var/log/controlhub.log"}},\n'
               '      {error_logger_mf_dir, "/var/log/controlhub"},\n'
               '      {error_logger_mf_maxbytes, 10485760},\n'
               '      {error_logger_mf_maxfiles, 4}\n'
               '    ]\n'
               '  },\n'
               '  {controlhub,\n'
               '    [{max_in_flight, ' + str(max_in_flight) + '}]\n'
               '  }\n'
               '].\n')
    
    SCRIPT_LOGGER.info('ControlHub (remote) sys.config file at "' + sys_config_location + '" is being updated to have max_in_flight=' + str(max_in_flight) + '. New contents is ...\n' + content)
 
    with tempfile.NamedTemporaryFile('w+b', suffix="__ch_sys.config") as tmp_file:
        tmp_file.write(content)
        tmp_file.flush()

        sftp_client = ssh_client.open_sftp()
        sftp_client.put(tmp_file.name, sys_config_location)
        sftp_client.close()     


####################################################################################################
#  FUNCTIONS RUNNING SEQUENCES OF TESTS

class DataseriesStats(object):
    def __init__(self):
        self.mean = []
        self.mean_err_lo = []
        self.mean_err_hi = []
        self.rms = []
        self.rms_err_lo = []
        self.rms_err_hi = []

    def mean_errors(self):
        return [self.mean_err_lo, self.mean_err_hi]

    def rms_errors(self):
        return [self.rms_err_lo, self.rms_err_hi]


def calc_y_stats(data_dict):
    stats = DataseriesStats()
    for x in sorted(data_dict.keys()):
        y_values = data_dict[x]
        mean, mean_err = calc_mean_with_error(y_values)
        rms, rms_err = calc_rms_with_error(y_values)
        stats.mean.append(mean)
        stats.mean_err_lo.append(mean_err)
        stats.mean_err_hi.append(mean_err)
        stats.rms.append(rms/mean)
        stats.rms_err_lo.append(rms_err/mean)
        stats.rms_err_hi.append(rms_err/mean)

    return stats
    
def calc_mean_with_error(data_list):
    mean = numpy.mean(data_list)
    n = len(data_list)
    bootstrap_sample_means = []
    for i in range(100):
        sum = 0.0
        for i in range(n):
            sum += data_list[randint(0,n-1)]
        bootstrap_sample_means.append( sum/n )
    err = calc_rms( bootstrap_sample_means )
    # rms = sqrt( sum([(y-mean)**2 for y in data_list]) / len(data_list) )
    return mean, err


def calc_rms(data_list):
    mean = numpy.mean(data_list)
    mean_sq_diff = sum([ (y-mean)**2 for y in data_list ]) / len(data_list)
    return sqrt( mean_sq_diff )

def calc_rms_with_error(data_list):
    rms = calc_rms(data_list)
    # Bootstrap for error
    n = len(data_list)
    bootstrap_sample_rms = []
    for i in range(100):
        min, max = 0.0, 0.0
        sum, sum2 = 0.0, 0.0
        for j in range(n):
            val = data_list[randint(0,n-1)]
            sum += val
            sum2 += val ** 2
            if j == 0:
                min, max = val, val
            elif val < min:
                min = val
            elif val > max:
                max = val
        mean = sum/n
        if min == max:
           bootstrap_sample_rms.append ( 0.0 )
        else:
           bootstrap_sample_rms.append ( sqrt(sum2/n - mean*mean) )
    err = calc_rms( bootstrap_sample_rms )
    return rms, err


def measure_latency(target, controhub_ssh_client, ax):
    '''Measures latency for single word write to given endpoint'''
    print "\n ---> MEASURING LATENCY TO '" + target + "' <---"
    print time.strftime('%l:%M%p %Z on %b %d, %Y')

#    # Initialise vars to store latency measurements:
#    lats_ping_uhal_ch, lats_ping_ch_target, lats_ping_uhal_target = [], [], []
#    lats_ipbusudp_uhalpc, lats_ipbusudp_chpc = [], []
#    lats_chtcp = []
#
#    for i in range(5):
#       lats_ping_uhal_ch.append( run_ping(CH_PC_NAME) )
#       lats_ping_ch_target.append( run_ping(target, ssh_client=controlhub_ssh_client) )
#       lats_ping_uhal_target.append( run_ping(target) )
#       lats_ipbusudp_uhalpc.append( run_command("PerfTester.exe -t BandwidthTx -b 0x1000 -w 1 -i 1000 -p -d ipbusudp-2.0://"+target)[0] ) 
#       lats_ipbusudp_chpc.append( run_command("PerfTester.exe -t BandwidthTx -b 0x1000 -w 1 -i 1000 -p -d ipbusudp-2.0://"+target, ssh_client=controlhub_ssh_client)[0] )
#       start_controlhub(controlhub_ssh_client)
#       lats_chtcp.append( run_command("PerfTester.exe -t BandwidthTx -b 0x1000 -w 1 -i 1000 -p -d chtcp-2.0://"+CH_PC_NAME+":10203?target="+target)[0] )
#       stop_controlhub(controlhub_ssh_client)    

#    positions, vals, errs, labels = [], [], [], []
    
#    def analyse( latencies_list, label):
#        if len(positions) == 0:
#            positions.append( 0.5 )
#        else:
#            positions.append( positions[-1] - 1.0 )
#        mean, rms = calc_mean_with_error(latencies_list)
#        vals.append( mean )
#        errs.append( rms )
#        labels.append( label )

#    analyse( lats_ping_uhal_ch , 'Ping\nuHAL to CH PC')
#    analyse( lats_ping_ch_target, 'Ping\nCH PC to board')
#    analyse( lats_ping_uhal_target, 'Ping\nuHAL PC to board')
#    analyse( lats_ipbusudp_uhalpc, 'Direct UDP\nFrom uHAL PC')
#    analyse( lats_ipbusudp_chpc, 'Direct UDP\nFrom CH PC')
#    analyse( lats_chtcp, 'Via ControlHub')

#    axis.barh(positions, vals, xerr=errs, ecolor='black', align='center')
#    axis.set_xlabel('Latency [us]')
#    axis.set_yticks(positions)
#    axis.set_yticklabels(labels)
#    axis.grid(True) 

    depths = [1, #2, 3, 4, 5, 6, 7, 8, 9, 10,
              50, 100, 150, 200, 250, 300, 342, 345, 400, 450, 500, 550, 600, 680, 690, 800, 900, 1000
#              75, 100, 150, 175, 200, 250, 300, 350, 400, 
#              500, 600, 700, 800, 900, 1000
             ]

    ch_uri = "chtcp-2.0://" + CH_PC_NAME + ":10203?target=" + target
    ch_tx_lats = dict((x, []) for x in depths)
    ch_rx_lats = dict((x, []) for x in depths)

    start_controlhub(controlhub_ssh_client)
    for i in range(10):
        for d in depths:
            itns = 1000
            ch_tx_lats[d].append( run_command("PerfTester.exe -t BandwidthTx -b 0x2001 -w "+str(d)+" -p -i "+str(itns)+" -d "+ch_uri)[0] )
            ch_rx_lats[d].append( run_command("PerfTester.exe -t BandwidthRx -b 0x2001 -w "+str(d)+" -p -i "+str(itns)+" -d "+ch_uri)[0] )
    stop_controlhub(controlhub_ssh_client)

    ch_tx_lats_mean, ch_tx_lats_yerrors = calc_y_with_errors(ch_tx_lats)
    ch_rx_lats_mean, ch_rx_lats_yerrors = calc_y_with_errors(ch_rx_lats)

    ax.errorbar(depths, ch_tx_lats_mean, yerr=ch_tx_lats_yerrors, label="Block write")
    ax.errorbar(depths, ch_rx_lats_mean, yerr=ch_rx_lats_yerrors, label="Block read")

    ax.set_xlabel("Number of words")
    ax.set_ylabel("Mean latency [us]")
#    plt.xscale("log")
    ax.legend(loc='lower right')



def measure_bw_vs_depth(target, controlhub_ssh_client, ax):
    '''Measures bandwidth for block writes to given endpoint'''
    print "\n ---> MEASURING BANDWIDTH vs DEPTH to '" + target + "' <---"
    print time.strftime('%l:%M%p %Z on %b %d, %Y')

    fig_bw = plt.figure(figsize=(12,10))
    ax_bw1 = fig_bw.add_subplot(221)
    ax_bw2 = fig_bw.add_subplot(222, xscale='log')
    ax_bw3 = fig_bw.add_subplot(223)
    ax_bw4 = fig_bw.add_subplot(224, xscale='log')

    fig_lat = plt.figure(figsize=(12,10))
    ax_lat1 = fig_lat.add_subplot(221)
    ax_lat2 = fig_lat.add_subplot(222)
    ax_lat3 = fig_lat.add_subplot(223)
    ax_lat4 = fig_lat.add_subplot(224)

    depths = [1]
    depths += [50, 100, 150, 200, 250, 300, 340, 350, 500, 600, 680, 700, 800, 900, 1000]
    for n in range(1, 10):
        depths += [3*n*343] # depths += [(3*n-1)*343, (3*n-1)*343+1, 3*n*343, 3*n*343+1]
    for n in range(10, 31, 2) + range(40, 80, 20) + range(80, 200, 40):
        depths += [3*n*343]
    for n in [2e2, 3e2, 4e2, 7e2, 10e2]:
        depths += [3*n*343]
    for n in [2e3, 3e3, 4e3, 7e3, 10e3]:
        depths += [3*n*343]

#    depths += range(50, 2000, 50)
#    depths += range(2000, 20000, 100)
#    depths += [3e4, 4e4, 5e4, 6e4, 7e4, 8e4, 
#              1e5, 1.2e5, 1.4e5, 1.6e5, 2e5, 3e5, 4e5, 5e5, 6e5, 7e5, 8e5,
#              1e6, 2.5e6, 5e6,
#              9999999] 

#    udp_bandwidths = dict((x, []) for x in depths)
#    udp_uri = "ipbusudp-2.0://" + target
#    for i in range(20):
#        for d in depths:
#            itns = 1
#            udp_bandwidths[d].append( run_command("PerfTester.exe -t BandwidthTx -b 0x2001 -w "+str(d)+" -p -i "+str(itns)+" -d "+udp_uri)[1] )
#    udp_bws_mean, udp_bws_yerrors = calc_y_with_errors(udp_bandwidths)
#    ax.errorbar(depths, udp_bws_mean, yerr=udp_bws_yerrors, label='Writes, direct UDP')

    ch_uri = "chtcp-2.0://" + CH_PC_NAME + ":10203?target=" + target
    for nr_in_flight in [16]:
        ch_tx_bws_excl_connect = dict((x, []) for x in depths)
        ch_tx_bws_incl_connect = dict((x, []) for x in depths)
        ch_rx_bws_excl_connect = dict((x, []) for x in depths)
        ch_rx_bws_incl_connect = dict((x, []) for x in depths)

        ch_tx_lats_excl_connect = dict((x, []) for x in depths)
        ch_tx_lats_incl_connect = dict((x, []) for x in depths)
        ch_rx_lats_excl_connect = dict((x, []) for x in depths)
        ch_rx_lats_incl_connect = dict((x, []) for x in depths)

        update_controlhub_sys_config(nr_in_flight, controlhub_ssh_client, CH_SYS_CONFIG_LOCATION)
        start_controlhub(controlhub_ssh_client)
        for i in range(40):
            SCRIPT_LOGGER.warning('iteration %d' % i)
            for d in depths:
                itns = {True : 10, False : 1}[i<1000]
                cmd_tx = "PerfTester.exe -t BandwidthTx -b 0x2001 -w "+str(int(d))+" -p -i "+str(itns)+" -d "+ch_uri
                cmd_rx = cmd_tx.replace("BandwidthTx", "BandwidthRx")
                ch_tx_lats_excl_connect[d].append( run_command( cmd_tx )[0] )
#                ch_tx_lats_incl_connect[d].append( run_command( cmd_tx + " --includeConnect" )[0] )
                ch_rx_lats_excl_connect[d].append( run_command( cmd_rx )[0] )
#                ch_rx_lats_incl_connect[d].append( run_command( cmd_rx + " --includeConnect" )[0] )

        stop_controlhub(controlhub_ssh_client)

        for d in depths:
            ch_tx_lats_excl_connect[d].sort()
            ch_tx_lats_incl_connect[d].sort()
            ch_rx_lats_excl_connect[d].sort()
            ch_rx_lats_incl_connect[d].sort()
            for i in range( int( ceil( 0.08 * len(ch_tx_lats_excl_connect[d]) ) ) ):
                SCRIPT_LOGGER.warning('Removing "high latency" outlier from depth=%d measurements' % (d))
                if d == 1:
                    print "Latency being removed is:", ch_tx_lats_excl_connect[d][-1]
                ch_tx_lats_excl_connect[d].pop() 
#                ch_tx_lats_incl_connect[d].pop()
                ch_rx_lats_excl_connect[d].pop()
#                ch_rx_lats_incl_connect[d].pop()
            ch_tx_bws_excl_connect[d] = [ 1e-3 * 32 * d / t for t in ch_tx_lats_excl_connect[d] ]
            ch_tx_bws_incl_connect[d] = [ 1e-3 * 32 * d / t for t in ch_tx_lats_incl_connect[d] ]
            ch_rx_bws_excl_connect[d] = [ 1e-3 * 32 * d / t for t in ch_rx_lats_excl_connect[d] ]
            ch_rx_bws_incl_connect[d] = [ 1e-3 * 32 * d / t for t in ch_rx_lats_incl_connect[d] ]

        tx_rtt = 0.0
        tx_t_send = 0.0
        for meas_bws, meas_lats, label in [(ch_tx_bws_excl_connect, ch_tx_lats_excl_connect, 'Write'),
#                                           (ch_tx_bws_incl_connect, ch_tx_lats_incl_connect, 'Write, incl. connect'), 
                                           (ch_rx_bws_excl_connect, ch_rx_lats_excl_connect, 'Read') ]: 
#                                           (ch_rx_bws_incl_connect, ch_rx_lats_incl_connect, 'Read, incl. connect')]:
            bws_stats = calc_y_stats(meas_bws)
            lats_stats = calc_y_stats(meas_lats)
            for (ax_mean, ax_rms) in [(ax_bw1,ax_bw3), (ax_bw2,ax_bw4)]:
                ax_mean.errorbar(depths, bws_stats.mean, yerr=bws_stats.mean_errors(), label=label)
                ax_rms.errorbar(depths, bws_stats.rms, yerr=bws_stats.rms_errors(), label=label)
            for (ax_mean, ax_rms) in [(ax_lat1,ax_lat3), (ax_lat2,ax_lat4)]:
                ax_mean.errorbar(depths, lats_stats.mean, yerr=lats_stats.mean_errors(), label=label)
                ax_rms.errorbar(depths, lats_stats.rms, yerr=lats_stats.rms_errors(), label=label)

#        tx_rtt = 3*343*32 / ( 1000.0 * numpy.mean( ch_tx_bws[3*343] ) )
#        tx_t_max = 2 * 3*343*32 * ( 1 / numpy.mean(ch_tx_bws[2*3*343]) - 0.5 / numpy.mean(ch_tx_bws[3*343]) ) / 1000.0
#        print " --> PREDICTION PARAMS ..."
#        print "       tx_rtt   =", tx_rtt, "us"
#        print "       tx_t_max =", tx_t_max, "us"
#        xx = numpy.arange(343.0, 200000.0, 0.1)
#        tx_prediction = [ (x/1029.0)*32.928 / (tx_rtt + ((x/1029.0)-1)*tx_t_max) for x in xx]

        for ax in [ax_bw1, ax_bw2, ax_bw3, ax_bw4, ax_lat1, ax_lat2, ax_lat3, ax_lat4]:
            ax.set_xlabel("Number of words")
            ax.set_ylim(0.0)

        for ax in [ax_bw1, ax_bw2, ax_lat1, ax_lat2]:
#            ax.plot(xx, tx_prediction, ':', label='Write prediction')
            if (ax is ax_bw1) or (ax is ax_bw2):
                ax.set_ylabel('Mean throughput [Gbit/s]')
            else:
                ax.set_ylabel('Mean latency [us]')
            leg = ax.legend(loc='best', fancybox=True)
            leg.get_frame().set_alpha(0.5)
        for ax in [ax_bw3, ax_bw4, ax_lat3, ax_lat4]:
            ax.set_ylabel('Fractional RMS variation')

        for ax in [ax_bw1, ax_bw3, ax_lat1, ax_lat3]:
            ax.set_xlim(0, 1001) # 30001)
#            for d in range(0, 10000, 343):
#                ax.axvline(d, color='DarkGrey', linestyle='-.')
        ax_lat1.set_ylim(200, 600) # 0, 4500)

        for ax in [ax_bw2, ax_bw4]:
            ax.set_xlim(999, 1.001e7)
        for ax in [ax_lat2, ax_lat4]:
            ax.set_xlim(0, 1.001e6)
        ax_lat2.set_ylim(0, 1e5)

        
        print "1-word latencies ... tx w/o connect ; rx w/o connect; tx w/ connect ; tx w/o connect ..."
        for tx_excl in sorted(ch_tx_lats_excl_connect[1]): #, rx_excl, tx_incl, rx_incl in 
                  #zip( sorted(ch_tx_lats_excl_connect[1]),
                  #sorted(ch_rx_lats_excl_connect[1]),
                  #sorted(ch_tx_lats_incl_connect[1]),
                  #sorted(ch_rx_lats_incl_connect[1]) 
                  #):
            #print "  {0:7.1f}  {1:7.1f}  {2:7.1f}  {2:7.1f}".format(tx_excl, rx_excl, tx_incl, rx_incl)
            print "  {0:7.2f}".format(tx_excl)


def measure_bw_vs_nInFlight(target, controlhub_ssh_client, ax):
    '''Measures continuous write/read bandwidth for block writes to given endpoint'''
    print "\n ---> BANDWIDTH vs NR_IN_FLIGHT to '" + target + "' <---"
    print time.strftime('%l:%M%p %Z on %b %d, %Y')

    nrs_in_flight = [4,8,10,12,16] # [1,2,3,4,6,8,10,12,14,16]
    cmd_base = "PerfTester.exe -w 5000000 -b 0x2001 -i 1 -d chtcp-2.0://" + CH_PC_NAME + ":10203?target=" + target
    print cmd_base
#    cmd_base = "perf_tester.escript tcp_ch_client2 " + CH_PC_NAME + " " + target.replace(":"," ") + " 1600 6"
#    cmd_base = "perf_tester.escript tcp_ch_client " + CH_PC_NAME + " " + target.replace(":"," ") + " 16000 50"

    ch_tx_bws = dict((x, []) for x in nrs_in_flight)
    ch_tx_bws_3 = dict((x, []) for x in nrs_in_flight)
    ch_tx_bws_4 = dict((x, []) for x in nrs_in_flight)
    ch_rx_bws  = dict((x, []) for x in nrs_in_flight)
    ch_rx_bws_3 = dict((x, []) for x in nrs_in_flight)
    ch_rx_bws_4 = dict((x, []) for x in nrs_in_flight)

    for i in range(10):
        SCRIPT_LOGGER.warning( "Starting iteration %d" % i )
        for n in nrs_in_flight:
            update_controlhub_sys_config(n, controlhub_ssh_client, CH_SYS_CONFIG_LOCATION)

            start_controlhub(controlhub_ssh_client)
            ch_tx_bws[n].append( run_command(cmd_base + " -t BandwidthTx")[1] / 1000.0 )
            stop_controlhub(controlhub_ssh_client)

            start_controlhub(controlhub_ssh_client)
            ch_tx_bws_3[n].append( run_command(cmd_base.replace("chtcp-2.0","chtcp-2.0-3perSend") + " -t BandwidthTx")[1] / 1000.0 )
            stop_controlhub(controlhub_ssh_client)

            start_controlhub(controlhub_ssh_client)
            ch_tx_bws_4[n].append( run_command(cmd_base.replace("chtcp-2.0","chtcp-2.0-4perSend") + " -t BandwidthTx")[1] / 1000.0 )
            stop_controlhub(controlhub_ssh_client)

            start_controlhub(controlhub_ssh_client)
            ch_rx_bws[n].append( run_command(cmd_base + " -t BandwidthRx")[1] / 1000.0 )
            stop_controlhub(controlhub_ssh_client)

            start_controlhub(controlhub_ssh_client)
            ch_rx_bws_3[n].append ( run_command(cmd_base.replace("chtcp-2.0","chtcp-2.0-3perSend") + " -t BandwidthRx")[1] / 1000.0 )
            stop_controlhub(controlhub_ssh_client)

            start_controlhub(controlhub_ssh_client)
            ch_rx_bws_4[n].append ( run_command(cmd_base.replace("chtcp-2.0","chtcp-2.0-4perSend") + " -t BandwidthRx")[1] / 1000.0 )
            stop_controlhub(controlhub_ssh_client)


    ch_tx_bws_stats   = calc_y_stats(ch_tx_bws)
    ch_tx_bws_3_stats = calc_y_stats(ch_tx_bws_3)
    ch_tx_bws_4_stats = calc_y_stats(ch_tx_bws_4)
    ch_rx_bws_stats   = calc_y_stats(ch_rx_bws)
    ch_rx_bws_3_stats = calc_y_stats(ch_rx_bws_3)
    ch_rx_bws_4_stats = calc_y_stats(ch_rx_bws_4)

    ax.errorbar(nrs_in_flight, ch_tx_bws_stats.mean, yerr=ch_tx_bws_stats.mean_errors(), label='20MB write, 15')
    ax.errorbar(nrs_in_flight, ch_tx_bws_3_stats.mean, yerr=ch_tx_bws_3_stats.mean_errors(), label='20MB write, 3')
    ax.errorbar(nrs_in_flight, ch_tx_bws_4_stats.mean, yerr=ch_tx_bws_4_stats.mean_errors(), label='20MB write, 4')
    ax.errorbar(nrs_in_flight, ch_rx_bws_stats.mean, yerr=ch_rx_bws_stats.mean_errors(), fmt='--', label='20MB read, 15')
    ax.errorbar(nrs_in_flight, ch_rx_bws_3_stats.mean, yerr=ch_rx_bws_3_stats.mean_errors(), fmt='--', label="20MB read, 3")
    ax.errorbar(nrs_in_flight, ch_rx_bws_4_stats.mean, yerr=ch_rx_bws_4_stats.mean_errors(), fmt='--', label="20MB read, 4")
    ax.set_xlabel('Number in flight over UDP')
    ax.set_ylabel('Mean bandwidth [Gbit/s]')
    ax.set_ylim(0, 0.65)
    ax.legend(loc='top right')


def measure_bw_vs_nClients(targets, controlhub_ssh_client):
    '''Measures continuous block-write bandwidth from to all endpoints, varying the number of clients.'''
    print "\n ---> BANDWIDTH vs NR_CLIENTS to", targets, "<---"
    print time.strftime('%l:%M%p %Z on %b %d, %Y')

    cmd_base = "PerfTester.exe -t BandwidthTx -w 3840 -d chtcp-2.0://" + CH_PC_NAME + ":10203?target="
#    cmd_base = "perf_tester.escript tcp_ch_client2 " + CH_PC_NAME + " "
#    cmd_base = "perf_tester.escript tcp_ch_client " + CH_PC_NAME + " "
    cmd_runner = CommandRunner( [('PerfTester.exe',None), ('beam.smp',controlhub_ssh_client)] )
#    cmd_runner = CommandRunner( [('beam.smp',None), ('beam.smp',controlhub_ssh_client)] )

    nrs_clients = [1,2,3,4,5,6,8]
    nrs_targets = range(1, len(targets)+1)

    bws_per_board  = dict( ((x,z), []) for x in nrs_clients for z in nrs_targets )
    bws_per_client = dict( ((x,z), []) for x in nrs_clients for z in nrs_targets )
    total_bws      = dict( ((x,z), []) for x in nrs_clients for z in nrs_targets )
    ch_cpu_vals = dict( ((x,z), []) for x in nrs_clients for z in nrs_targets )
    ch_mem_vals = dict( ((x,z), []) for x in nrs_clients for z in nrs_targets )
    uhal_cpu_vals = dict( ((x,z), []) for x in nrs_clients for z in nrs_targets )
    uhal_mem_vals = dict( ((x,z), []) for x in nrs_clients for z in nrs_targets )
    
    update_controlhub_sys_config(16, controlhub_ssh_client, CH_SYS_CONFIG_LOCATION)
    start_controlhub(controlhub_ssh_client)

    for i in range(5):
        for n_clients in nrs_clients:
            for n_targets in nrs_targets:
                itns = int(20480/(n_clients*n_targets))
                cmds = [cmd_base + t + ' -i ' + str(itns) for t in targets[0:n_targets] for x in range(n_clients)]
#               itns = int(25000/(n_clients*n_targets))
#                itns = int(250000/(n_clients*n_targets))
#                cmds = [cmd_base + t.replace(":"," ") + ' ' + str(itns) + " 50" for t in targets[0:n_targets] for x in range(n_clients)]
                monitor_results, cmd_results = cmd_runner.run(cmds)
                bws = [ (x[1]/1000.0) for x in cmd_results]

                dict_idx = (n_clients, n_targets)
                uhal_cpu_vals[dict_idx].append( monitor_results[0][1] )
                uhal_mem_vals[dict_idx].append( monitor_results[0][2] )
                ch_cpu_vals[dict_idx].append( monitor_results[1][1] )
                ch_mem_vals[dict_idx].append( monitor_results[1][2] )
                bws_per_board[dict_idx].append( sum(bws)/n_targets )
                bws_per_client[dict_idx].append( sum(bws)/len(bws) )
                total_bws[dict_idx].append( sum(bws) )
        
    stop_controlhub(controlhub_ssh_client)

    fig = plt.figure(figsize=(18,10))
    ax_bw_board = fig.add_subplot(231)
    ax_bw_total = fig.add_subplot(234)
    ax_ch_cpu = fig.add_subplot(232, ylim=[0,400])
    ax_ch_mem = fig.add_subplot(235, ylim=[0,100])
    ax_uhal_cpu = fig.add_subplot(233, ylim=[0,400])
    ax_uhal_mem = fig.add_subplot(236, ylim=[0,100])
    fig.suptitle('100MB continuous write to crate')

    for n_targets in nrs_targets:
        label = str(n_targets) + ' targets'
        bws_per_board_mean , bws_per_board_errs  = calc_y_with_errors( dict(x for x in bws_per_board.items() if x[0][1]==n_targets) )
        bws_per_client_mean, bws_per_client_errs = calc_y_with_errors( dict(x for x in bws_per_client.items() if x[0][1]==n_targets) )
        total_bws_mean, total_bws_errs           = calc_y_with_errors( dict(x for x in total_bws.items() if x[0][1]==n_targets) )
        ch_cpu_mean, ch_cpu_errs = calc_y_with_errors( dict(x for x in ch_cpu_vals.items() if x[0][1]==n_targets) )
        ch_mem_mean, ch_mem_errs = calc_y_with_errors( dict(x for x in ch_mem_vals.items() if x[0][1]==n_targets) )
        uhal_cpu_mean, uhal_cpu_errs = calc_y_with_errors( dict(x for x in uhal_cpu_vals.items() if x[0][1]==n_targets) )
        uhal_mem_mean, uhal_mem_errs = calc_y_with_errors( dict(x for x in uhal_mem_vals.items() if x[0][1]==n_targets) )

        print label
        for cpu in ch_cpu_mean:
            print "ControlHub cpu:", cpu

        ax_bw_board.errorbar(nrs_clients, bws_per_board_mean, yerr=bws_per_board_errs, label=label)
#        ax_bw_client.errorbar(nrs_clients, bws_per_client_mean, yerr=bws_per_client_errs, label=label)
        ax_bw_total.errorbar(nrs_clients, total_bws_mean, yerr=total_bws_errs, label=label)
        ax_ch_cpu.errorbar(nrs_clients, ch_cpu_mean, yerr=ch_cpu_errs, label=label)
        ax_ch_mem.errorbar(nrs_clients, ch_mem_mean, yerr=ch_mem_errs, label=label)
        ax_uhal_cpu.errorbar(nrs_clients, uhal_cpu_mean, yerr=uhal_cpu_errs, label=label)
        ax_uhal_mem.errorbar(nrs_clients, uhal_mem_mean, yerr=uhal_mem_errs, label=label)
        
    for ax in [ax_bw_board, ax_bw_total, ax_ch_cpu, ax_ch_mem, ax_uhal_cpu, ax_uhal_mem]:
        ax.set_xlabel('Number of clients per board')

    ax_bw_board.set_ylabel('Total bandwidth per board [Gbit/s]')
#    ax_bw_client.set_ylabel('Bandwidth per client [Mb/s]')
    ax_bw_total.set_ylabel('Total bandwidth [Gbit/s]')
    for ax in [ax_bw_board, ax_bw_total]:
        ax.set_ylim(0, 0.9)

    ax_ch_cpu.set_ylabel('ControlHub CPU usage [%]')
    ax_ch_mem.set_ylabel('ControlHub memory usage [%]')
    ax_uhal_cpu.set_ylabel('uHAL client CPU usage [%]')
    ax_uhal_mem.set_ylabel('uHAL client memory usage [%]')
    for ax in [ax_ch_cpu, ax_uhal_cpu]:
        ax.set_ylim(0,400)
    for ax in [ax_ch_mem, ax_uhal_mem]:
        ax.set_ylim(0)

    ax_bw_total.legend(loc='lower right')
#    ax.set_title('a title')


####################################################################################################
#  MAIN

if __name__ == "__main__":
    controlhub_ssh_client = ssh_into( CH_PC_NAME, CH_PC_USER )

#    (f, ((ax1, ax2), (ax3, ax4))) = plt.subplots(2, 2)
    f = plt.figure(figsize=(12,10))
    ax2 = f.add_subplot(222)
    ax3 = f.add_subplot(223)
    ax4 = f.add_subplot(224)

    f.text(0.06, 0.9, "Measurements @ " + time.strftime('%l:%M%p %Z on %b %d, %Y'))
    f.text(0.06, 0.85, "Version:\n    " + CACTUS_REVISION)
    f.text(0.06, 0.8, "Changes:\n    " + CHANGES_TAG) 
    f.text(0.06, 0.75, "ControlHub host: " + CH_PC_NAME)
    f.text(0.06, 0.7, "uHAL host: " + UHAL_PC_NAME)    
    f.text(0.06, 0.65, "Targets: \n    " + "\n    ".join(TARGETS), verticalalignment='baseline')

#    measure_latency(TARGETS[0], controlhub_ssh_client, ax2)

#    measure_bw_vs_nInFlight(TARGETS[0], controlhub_ssh_client, ax3)

    measure_bw_vs_depth(TARGETS[0], controlhub_ssh_client, ax4)

#    plt.savefig('plot1.png') 

#    measure_bw_vs_nClients(TARGETS, controlhub_ssh_client)

#    plt.savefig('plot2.png')

    print time.strftime('%l:%M%p %Z on %b %d, %Y')
    plt.show()
