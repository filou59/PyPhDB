import argparse
import os
import re
import shutil
import subprocess
import sys
import sqlite3
import validators


class PyPhDB:

    def __init__(self, ph_dir='/etc/pihole/'):

        self.path_pihole_dir = '/etc/pihole/' if not ph_dir else os.path.expanduser(ph_dir)
        self.path_pihole_db = os.path.join(self.path_pihole_dir, 'gravity.db')
        self.path_output_dir = os.path.join(self.path_pihole_dir, 'PyPhDB')
        self.connection = None
        self.cursor = None

        self.set_adlists = set()
        self.set_blacklist = set()
        self.set_whitelist = set()
        self.set_bl_regexps = set()
        self.set_wl_regexps = set()
        self.set_gravity = set()

    def access_check(self):

        if os.path.exists(self.path_pihole_dir):
            print('[i] Pi-hole directory located')
            if os.access(self.path_pihole_dir, os.X_OK | os.W_OK):
                print('[i] Write access is available to Pi-hole directory')
                # Does the DB exist
                # and is the file size greater than 0 bytes
                if os.path.isfile(self.path_pihole_db) and os.path.getsize(self.path_pihole_db) > 0:
                    print('[i] Pi-hole DB located')
                    return True
                else:
                    print('[e] Write access is available but the Pi-hole DB does not exist')
                    return False
            else:
                print('[e] Write access is not available to the Pi-hole directory.')
                return False
        else:
            print(f'[e] {self.path_pihole_dir} does not exist.')
            return False

    def make_connection(self):

        try:
            self.connection = sqlite3.connect(self.path_pihole_db)
        except sqlite3.Error as e:
            print('[e] Failed to connected to Pi-hole DB')
            return False

        print('[i] Connection established to Pi-hole DB')

        self.cursor = self.connection.cursor()

        return True

    def close_connection(self):

        print('[i] Closing connection to the Pi-hole DB')
        self.connection.close()

    def fetch_data(self):

        # adlists.list
        print('[i] Fetching adlists')
        self.cursor.execute('SELECT address FROM adlist')
        self.set_adlists.update(x[0] for x in self.cursor.fetchall())

        # whitelist.list
        print('[i] Fetching whitelist')
        self.cursor.execute('SELECT domain FROM domainlist WHERE type = 0')
        self.set_whitelist.update(x[0] for x in self.cursor.fetchall())

        # blacklist.list
        print('[i] Fetching blacklist')
        self.cursor.execute('SELECT domain FROM domainlist WHERE type = 1')
        self.set_blacklist.update(x[0] for x in self.cursor.fetchall())

        # whitelist_regex.list
        print('[i] Fetching whitelist regexps')
        self.cursor.execute('SELECT domain FROM domainlist WHERE type = 2')
        self.set_wl_regexps.update(x[0] for x in self.cursor.fetchall())

        # regex.list
        print('[i] Fetching blacklist regexps')
        self.cursor.execute('SELECT domain FROM domainlist WHERE type = 3')
        self.set_bl_regexps.update(x[0] for x in self.cursor.fetchall())

        # gravity.list
        print('[i] Fetching gravity domains')
        self.cursor.execute('SELECT distinct(domain) FROM gravity')
        self.set_gravity.update(x[0] for x in self.cursor.fetchall())

    def stage_output(self):

        # Create /etc/pihole/PyPhDB
        if not os.path.exists(self.path_output_dir):
            print('[i] Creating output directory')
            os.mkdir(self.path_output_dir)

    def dump_data(self):

        self.stage_output()

        # Create a dictionary for easier output
        dict_output = {
            'adlists.list': self.set_adlists,
            'whitelist.list': self.set_whitelist,
            'blacklist.list': self.set_blacklist,
            'whitelist_regex.list': self.set_wl_regexps,
            'regex.list': self.set_bl_regexps,
            'gravity.list': self.set_gravity
        }

        # Iterate through dictionary
        for k, v in dict_output.items():
            # Form the file path
            path_file = os.path.join(self.path_output_dir, k)

            print(f'[i] {k}:')
            print(f'    --> Outputting {len(v)} lines to {path_file}')

            with open(path_file, 'w') as fWrite:
                for line in sorted(v):
                    fWrite.write(f'{line}\n')

    def upload_files(self):

        # Create a dictionary for easier output
        dict_upload = {
            'adlists.list': self.set_adlists,
            'whitelist.list': self.set_whitelist,
            'blacklist.list': self.set_blacklist,
            'whitelist_regex.list': self.set_wl_regexps,
            'regex.list': self.set_bl_regexps
        }

        # Insert or IGNORE
        # Delete specifics
        # Delete all of type (if file cleared) with exception to adlists

        dict_sql = {
            'adlists.list':
            'INSERT OR IGNORE INTO adlist (address) VALUES (?)\
            |DELETE FROM adlist WHERE address IN (?)',
            'whitelist.list':
            'INSERT OR IGNORE INTO domainlist (type, domain, enabled) VALUES (0, ?, 1)\
            |DELETE FROM domainlist WHERE domain IN (?) AND type = 0\
            |DELETE FROM domainlist WHERE type = 0',
            'blacklist.list':
            'INSERT OR IGNORE INTO domainlist (type, domain, enabled) VALUES (1, ?, 1)\
            |DELETE FROM domainlist WHERE domain IN (?) AND type = 1\
            |DELETE FROM domainlist WHERE type = 1',
            'whitelist_regex.list':
            'INSERT OR IGNORE INTO domainlist (type, domain, enabled) VALUES (2, ?, 1)\
            |DELETE FROM domainlist WHERE domain IN (?) AND type = 2\
            |DELETE FROM domainlist WHERE type = 2',
            'regex.list':
            'INSERT OR IGNORE INTO domainlist (type, domain, enabled) VALUES (3, ?, 1)\
            |DELETE FROM domainlist WHERE domain IN (?) AND type = 3\
            |DELETE FROM domainlist WHERE type = 3'
        }

        # Determine how each list needs to be validated
        validators_adlist = {'adlists.list'}
        validators_domain = {'whitelist.list', 'blacklist.list'}
        validators_regexps = {'whitelist_regex.list', 'regex.list'}

        # For each upload item (list)
        for k, v in dict_upload.items():
            print(f'[i] Processing {k}')
            # Construct full file path
            path_file = os.path.join(self.path_output_dir, k)
            # Check if the file exists
            if os.path.isfile(path_file):
                # Create a new set to store changes
                set_modified = set()
                set_removal = set()
                # Read the file in the output directory to a set
                with open(path_file, 'r', encoding='utf-8', errors='ignore') as fOpen:
                    # Generator for selecting only non-empty lines / non-commented lines
                    lines = (x for x in map(str.strip, fOpen) if x and x[:1] != '#')
                    # Use appropriate validation when reading from the files
                    if k in validators_adlist:
                        # For each url
                        for line in lines:
                            # If it's a valid URL
                            if validators.url(line):
                                # Add to the set
                                set_modified.add(line)
                    elif k in validators_domain:
                        # For each domain
                        for line in lines:
                            # If it's a valid domain
                            if validators.domain(line):
                                # Add to the set
                                set_modified.add(line)
                    elif k in validators_regexps:
                        # For each regexp
                        for line in lines:
                            try:
                                # Try to compile the regexp (test if valid)
                                re.compile(line)
                                # If valid, add to set
                                set_modified.add(line)
                            except re.error:
                                # If invalid, skip to next
                                continue

                # If the set was populated
                if set_modified:
                    # Check if it's identical to DB
                    if set_modified == v:
                        print(' --> No Changes')
                    else:
                        print(' --> Updating DB')
                        # Update or Ignore
                        self.cursor.executemany(dict_sql[k].split('|')[0], [(x,) for x in set_modified])
                        # Find items that are in the DB but not in the modified files (for removal from db)
                        set_removal.update(x for x in v if x not in set_modified)
                        # If there are items to remove from the DB
                        if set_removal:
                            self.cursor.executemany(dict_sql[k].split('|')[1], [(x,) for x in set_removal])
                # If the file has been emptied
                else:
                    # Check whether the DB is already empty or not
                    if set_modified == v:
                        print(' --> No Changes')
                        continue
                    # Check if we've got a preset query to remove all
                    try:
                        sql_remove_all = dict_sql[k].split('|')[2]
                    except IndexError as e:
                        continue
                    # If we do, run it
                    if sql_remove_all:
                        print(' --> Updating DB')
                        self.cursor.execute(dict_sql[k].split('|')[2])
            else:
                print(' --> Local file does not exist')

        self.connection.commit()

    def clean_dump(self):

        if os.path.exists(self.path_output_dir):
            print('[i] Removing output directory')
            shutil.rmtree(self.path_output_dir)
        else:
            print('[i] Output directory does not exist')


def restart_pihole(docker=False):

    # Form the command to restart Pi-hole
    cmd = ['pihole', 'restartdns', 'reload']

    # If it's running in a docker container
    if docker:
        # Prepend list with docker commands
        cmd[0:0] = ['docker', 'exec']

    print('[i] Restarting Pi-hole')

    # Try to run the reset command
    try:
        subprocess.call(cmd, stdout=subprocess.DEVNULL)
    except OSError as e:
        print(f'[e] Restart failed: {e}')
        exit(1)


# Create a new argument parser
parser = argparse.ArgumentParser()
# Create mutual exclusion groups
group_action = parser.add_mutually_exclusive_group()
# Dump flag
group_action.add_argument('-d', '--dump', help='Export elements of the Pi-hole DB', action='store_true')
# Upload flag
group_action.add_argument('-u', '--upload', help='Import text files to the Pi-hole DB', action='store_true')
# Clean flag
group_action.add_argument('-c', '--clean', help='Clean (remove) the output directory', action='store_true')
# Add options group
group_options = parser.add_argument_group()
# Docker flag
group_options.add_argument('-dc', '--docker', help='Indicate that Pi-hole is being ran within a docker container',
                           action='store_true')
# Pi-hole DIR
group_options.add_argument('-dir', '--directory', help='Specify Pi-hole Directory')
# Parse arguments
args = parser.parse_args()

# If no arguments were passed
if not len(sys.argv) > 1:
    print('[i] No script arguments detected - Defaulted to DUMP')
    # Default to dump mode
    args.dump = True

# If the docker flag is enabled and no directory is specified
if args.docker and args.directory is None:
    parser.error('[e] If --docker is specified, you must also specify your Pi-hole volume location using --directory')

# Create a new instance
PyPhDB_inst = PyPhDB(ph_dir=args.directory)

# Access check for DB
if PyPhDB_inst.access_check():
    # If the clean flag is enabled
    if args.clean:
        PyPhDB_inst.clean_dump()
        exit()
    # If we're able to access the DB
    if PyPhDB_inst.make_connection():
        # Populate sets with data from DB
        PyPhDB_inst.fetch_data()
        # If the dump flag is enabled
        if args.dump:
            # Dump data to disk
            PyPhDB_inst.dump_data()
            # Close the connection to the DB
            PyPhDB_inst.close_connection()
        # If the upload flag is enabled
        elif args.upload:
            PyPhDB_inst.upload_files()
            # Close the connection to the DB
            PyPhDB_inst.close_connection()
            # Restart Pi-hole
            restart_pihole(docker=args.docker)
    else:
        exit(1)
else:
    exit(1)
