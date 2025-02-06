# encoding: utf-8
import sys
import re
import os
import xlrd
import logging
import psycopg2
import json
import docx
from pyexcel_ods import get_data
from docx import Document
import urllib.request, urllib.error, urllib.parse
import datetime
from ckan.logic import get_action
# from ckan.lib.cli import parse_db_config
from ckan.common import config
from ckan.config.environment import load_environment
from time import sleep
from pdfminer.pdfinterp import PDFResourceManager, PDFPageInterpreter
from pdfminer.converter import TextConverter
from pdfminer.layout import LAParams
from pdfminer.pdfpage import PDFPage
from io import StringIO
import ckan
import subprocess
import requests
import click

log = logging.getLogger(__name__)

# # # #
# Click commands
@click.command(name='cpr-scan')
def scan():
    resource_list = getAllResources()  # list of all resources in CKAN
    click.echo("%d resources in catalog \n" % len(resource_list))

    # Update the database with new resources and / or packages
    updateSchema(resource_list)

    # Fetch the resources that needs to be scanned
    resources_to_check = scanDB()
    count = 0

    if(len(resources_to_check) == 0):
        click.secho("No updates, no resources to scan", fg='yellow')
        return

    for r in resources_to_check:
        validateResource(r)
        count+= 1

    click.echo(("Scanned %d resources for CPR numbers" % count))

@click.command(name='cpr-initdb')
def initdb():
        # For debugging purposes we delete the database everytime we init. This CLEANS the database
        d_port = config.get('ckan.cprvalidation.postgres_port', None)
        d_pass = config.get('ckan.cprvalidation.cprvalidation_password', None)
        db_name = config.get('ckan.cprvalidation.cprvalidation_db', None)
        postgres_pass = config.get('ckan.cprvalidation.postgres_password', None)

        error_state = False
        if d_pass == None:
            click.echo("Setup cprvalidation_password in /etc/ckan/default/production.ini")
            error_state = True
        if d_port == None:
            click.echo("Setup postgres_port in /etc/ckan/default/production.ini")
            error_state = True
        if postgres_pass == None:
            click.echo("Setup postgres_password in /etc/ckan/default/production.ini")
            error_state = True

        if(error_state):
            click.secho("Exiting..", fg='red')
            sys.exit(1)

        create_user = '''
                    DROP ROLE IF EXISTS cprvalidation;
                    CREATE ROLE cprvalidation WITH PASSWORD %s LOGIN;
                '''
        drop_db = '''DROP DATABASE IF EXISTS {0};'''
        create_db = '''
            CREATE DATABASE {0}
            WITH OWNER = cprvalidation
            ENCODING = 'UTF8'
            TABLESPACE = pg_default
            CONNECTION LIMIT = -1;
        '''

        create_schema = '''
            DROP SCHEMA IF EXISTS {0};
            CREATE SCHEMA {0}
            AUTHORIZATION cprvalidation;
        '''

        create_table = '''
            DROP TABLE IF EXISTS {0}.status;
            CREATE TABLE {0}.status
            (
              package_id character varying NOT NULL,
              resource_id character varying NOT NULL,
              status character varying, -- valid, invalid, pending
              format character varying NOT NULL,
              url character varying,
              url_type character varying,
              datastore_active character varying,
              last_checked timestamp,
              last_updated timestamp,
              cpr_number character varying,
              excepted boolean,
              error character varying,
              CONSTRAINT status_pkey PRIMARY KEY (resource_id)
            )
            WITH (
              OIDS=FALSE
            );
            ALTER TABLE {0}.status
              OWNER TO cprvalidation;
            COMMENT ON COLUMN {0}.status.status IS 'valid, invalid, pending';

        '''

        try:
            host = config.get('POSTGRES_HOST', 'db')
            conn = psycopg2.connect(database="postgres", host=host, user="postgres", password=postgres_pass,
                                    port=d_port)
            conn.autocommit = True
            click.echo("Connected as postgres user.")
        except Exception as e:
            click.echo(e)
            sys.exit()

        cur = conn.cursor()
        try:
            cur.execute(drop_db.format(db_name))
            cur.execute(create_user,[d_pass])
            cur.execute(create_db.format(db_name))
            click.echo("Initialized Database")
            conn.commit()
            conn.close()
        except Exception as e:
            #TODO: Handle this sort of erros more gracefully
            click.echo("Unexpected error")
            click.echo((e.message))
            sys.exit(1)

        #
        # We need two different sessions to the database as we are changing user
        #
        try:
            host = config.get('POSTGRES_HOST', 'db')
            conn = psycopg2.connect(database=db_name, host=host, user="cprvalidation",
                                        password=d_pass,
                                        port=d_port)
            conn.autocommit = True

            click.secho("Created the table and scheme", fg='green')
        except Exception as e:
            click.secho("Woops", fg='red')
            click.secho(e, fg='red')
            sys.exit()

        cur = conn.cursor()
        try:
            cur.execute(create_schema.format(db_name))
            cur.execute(create_table.format(db_name))
            click.secho("Created schema and table", fg='green')
            conn.commit()
            conn.close()
            click.secho("Done.", fg='green')
        except:
            # TODO: Handle this sort of erros more gracefully
            click.echo("Unexpected error")
            sys.exit(1)

@click.command(name='cpr-addexception')
@click.option('--id', required=True, help='id to recongize relation')
def addexception(id):
    # Adds an exception to the database.
    # Sometimes resources will contain valid CPR-numbers which are in fact not
    d_port = config.get("ckan.cprvalidation.postgres_port", None)
    d_pass = config.get("ckan.cprvalidation.cprvalidation_password", None)
    db_name = config.get("ckan.cprvalidation.cprvalidation_db", None)

    add_exception = """ UPDATE {0}.status SET excepted = TRUE
                            WHERE package_id = %s
                            returning *
        ;"""

    if d_pass == None:
        print("Setup cprvalidation_password in /etc/ckan/default/production.ini")
        sys.exit(1)
    if d_port == None:
        print("Setup postgres_port in /etc/ckan/default/production.ini")
        sys.exit(1)

    try:
        # db_config = parse_db_config()
        host = "db"
        conn = psycopg2.connect(
            database=db_name,
            host=host,
            user="cprvalidation",
            password=d_pass,
            port=d_port,
        )
        conn.autocommit = True
        print(" ")
    except Exception as e:
        print(e)
        sys.exit()

    cur = conn.cursor()
    cur.execute(add_exception.format(db_name), (id,))

    count = len(cur.fetchall())
    if count == 0:
        print(("Could not find relation %s " % id))
    else:
        print(
            (
                "Added exception for %d resources in dataset with package_id: %s "
                % (count, id)
            )
        )

    conn.commit()
    conn.close()


# # # #
# # # #
# Helper Functions
# # # #
def processCSV(file_path, file_url, local):
    error = None
    file_string = None
    # We'll use the package_create function to create a new dataset.
    request = urllib.request.Request(
        'http://www.my_ckan_site.com/api/action/package_create')

    # Creating a dataset requires an authorization header.
    # Replace *** with your API key, from your user account on the CKAN site
    # that you're creating the dataset on.
    api = config.get('ckan.cprvalidation.api', None)

    # /var/lib/ckan/resources/757/465/8b-e8da-4de1-a7ce-7046cf1217aa'

    retrycount = 0
    #TODO: This is probably not the best way to handle a CSV file..
    if(local):
        with open(file_path) as f:
            file_string = f.read().replace(',', ' ')
    else:
        try:
            request = urllib.request.Request(file_url)

            request.add_header("Authorization", api)
            response = urllib.request.urlopen(request)
            file_string = response.read().replace(',',' ')
        except urllib.error.HTTPError as e:
            if e.code == 404:
                error = "404 file was not found"
            elif e.code == 500:
                print("500 Internal Server Error.. reconnecting")
                if(retrycount < 5):
                    for i in range(5):
                        try:
                            # We'll use the package_create function to create a new dataset.
                            request = urllib.request.Request(file_url)

                            request.add_header("Authorization", api)
                            response = urllib.request.urlopen(request)
                            file_string = response.read().replace(',', ' ')
                        except urllib.error.HTTPError as e:
                            retrycount += 1
                            print("Retrying...")
                        sleep(5)
                else: #We tried more than 5 times
                    error = "500 Internal Server Error"
            elif e.code == 504:
                error = "Gateway Timed Out, is file too big?"
            else:
                error = "Error: " + str(e.code)

    return [error,file_string]

def processDOCX(file_path):
    error = None
    file_string = None
    print("docx is being processed")
    try:
        # Download the file since it is url and Document expects local file path
        local_filename = file_path.split("/")[-1]
        response = requests.get(file_path)
        if response.status_code == 200:
            with open(local_filename, "wb") as f:
                f.write(response.content)
        
        doc = Document(local_filename)
        fullText = []
        for para in doc.paragraphs:
            fullText.append(para.text)
        file_string = '\n'.join(fullText)
        os.remove(local_filename)
    except Exception as e:
        error = str(e)

    print("docx is finished processed")
    return [error,file_string]

def processXLSX(file_url):
    error = None
    file_string = None
    #Uses Pandas to convert contents to string
    #Simple but it works
    #Parses all sheets by default
    data = []
    print("xls is being processed")

    try:
        socket = urllib.request.urlopen(file_url)
        #this line gets me the excel workbook
        xlfile = xlrd.open_workbook(file_contents = socket.read())
        sh1 = xlfile.sheet_by_index(0)
        for rownum in range(sh1.nrows): # sh1.nrows -> number of rows (ncols -> num columns)
            data.append(' '.join(sh1.row_values(rownum)))
    except xlrd.XLRDError as e:
        error = str(e)
    except Exception as e:
        error = e.message

    print("xls is finished processed")

    return [error, '\n'.join(data)]

def processPDF(file_path):
    #TODO: This method is generally too slow to be useful. Needs a rewrite (Add PDF as an allowed format in the SQL
    #TODO: query when done)

    print("pdf file is being processed")
    error = None
    file_string = None
    try:
        rsrcmgr = PDFResourceManager()
        retstr = StringIO()
        codec = 'utf-8'
        laparams = LAParams()
        device = TextConverter(rsrcmgr, retstr, codec=codec, laparams=laparams)
        fp = file(file_path, 'rb')
        interpreter = PDFPageInterpreter(rsrcmgr, device)
        password = ""
        maxpages = 0
        caching = True
        pagenos=set()

        for page in PDFPage.get_pages(fp, pagenos, maxpages=maxpages, password=password,caching=caching, check_extractable=True):
            print(page)
            interpreter.process_page(page)

        file_string = retstr.getvalue()

        print(file_string)

        fp.close()
        device.close()
        retstr.close()
    except Exception as e:
        error = e.message


    print("pdf file is finished processed")

    return [error,file_string]

def processJSON(file_url):
    error = None
    file_string = None
    print("json file is being processed")

    try:
        resp = requests.get(file_url)
        data = resp.json()
        file_string = str(data)
    except json.JSONDecodeError as e: # JSONDecodeError uses msg instead of message
        error = e.msg  
    except Exception as e: 
        error = e.message

    return [error,file_string]

def processODS(file_path):
    error = None
    print('ods is being processed')
    file_string = None
    '''Uses Pyexcel-ods to load the data as an OrderedDict, reads all sheets by default'''
    try:
        local_filename = file_path.split("/")[-1]
        response = requests.get(file_path)
        if response.status_code == 200:
            with open(local_filename, "wb") as f:
                f.write(response.content)
        

        data = get_data(local_filename)
        file_string = ' '.join([k + str(v) for k, v in list(data.items())])
        os.remove(local_filename)
    except Exception as e:
        error = str(e)

    print('ods is finished processed')
    return [error,file_string]

def validateResource(resource):
    '''       Overview of the tuple
         (
                 0 package_id character varying NOT NULL,
                 1 resource_id character varying NOT NULL,
                 2 status character varying, -- valid, invalid, pending
                 3 format character varying NOT NULL,
                 4 url character varying,
                 5 url_type character varying,
                 6 datastore_active character varying,
                 7 last_checked character varying,
                 8 last_updated character varying,
                 9 cpr_number character varying,
                 10 excepted bool,
                 11 error character varying
                )
        '''
    siteurl = config.get('ckan.site_url')
    email = config.get('ckan.cprvalidation.email', None)
    d_port = config.get('ckan.cprvalidation.postgres_port', None)
    d_pass = config.get('ckan.cprvalidation.cprvalidation_password', None)
    db_name = config.get('ckan.cprvalidation.cprvalidation_db', None)

    id = resource[1]
    format = str(resource[3]).lower()
    datastore = True if str(resource[6]).lower() == "true" else False
    filestore = True if resource[5] == "upload" else False
    file_url = resource[4]


    file_string = None
    file_path = None
    local = False
    error = False

    print("DEBUG INFO: ")
    print(("Resource: " + str(resource)))
    print(("Datastore: " + str(datastore)))
    print(("Filestore: " + str(filestore)))

    storage_path = config.get('ckan.storage_path')

    # Get the filepath, locally or externally, it should not matter
    if filestore:
        file_path = os.path.join(storage_path, 'resources',
                                 id[0:3], id[3:6], id[6:])
        local = True
    elif datastore:
        file_path = siteurl + "/datastore/dump/" + id + "?format=csv"
        format = "csv" #Datastore will always be CSV, so this makes it easier

    print(("Format: " + str(format)))
    print(("File_path: " + str(file_path)))

    if file_path is None:
        print("Could not construct file_path")
        return None

    format = str(format).lower()

    # If the s3filestore plugin is enabled, always retrieve files from HTTP
    if ckan.plugins.plugin_loaded('s3filestore'):
        local = False

    if format == "csv":
        output = processCSV(file_path, file_url, local)
    elif format == "docx":
        output = processDOCX(file_url)
    elif format == "ods":
        output = processODS(file_url)
    elif format == "xlsx":
        output = processXLSX(file_url)
    elif format == "pdf":
        output = processPDF(file_url)
    elif format == "geojson" or format == "json":
        output = processJSON(file_url)
    else:
        print(("Format %s can't be processed" % format))
        return

    error = output[0]
    file_string = output[1]
    insert_error = False

    if(file_string is None or error != None):
        insert_error = True
    else:
        iscpr = validcpr(file_string)


    if(insert_error):
        print(error)
        try:
            host = config.get('POSTGRES_HOST', 'db')
            conn = psycopg2.connect(database=db_name, host=host, user="cprvalidation", password=d_pass,
                                    port=d_port)
        except Exception as e:
            print(e)
            sys.exit()
        current_time = datetime.datetime.utcnow()  # Timestamp is UTC as CKAN stores metadata_modified as UTC
        insert = """
                    UPDATE {0}.status
                    SET status='error', last_checked = %s,error = %s
                    WHERE resource_id= %s
                    returning *
                ;"""

        cur = conn.cursor()
        cur.execute(insert.format(db_name), [current_time,error,id])
        conn.commit()
        conn.close()
    else:
        if(not iscpr[0]): #If we dont have a CPR in the resource
            try:
                host = config.get('POSTGRES_HOST', 'db')
                conn = psycopg2.connect(database=db_name,host=host, user="cprvalidation",password=d_pass,port=d_port)
            except Exception as e:
                print(e)
                sys.exit()
            current_time = datetime.datetime.utcnow() #Timestamp
            insert = """
                        UPDATE {0}.status
                        SET status='valid', last_checked= %s
                        WHERE resource_id= %s
                        returning *
                    ;"""

            cur = conn.cursor()
            cur.execute(insert.format(db_name), [current_time,id])
            conn.commit()
            conn.close()
        else: #We have a CPR-number!

            print("Detected a CPR number, if an exception is made nothing will happen")

            try:
                host = config.get('POSTGRES_HOST', 'db')
                conn = psycopg2.connect(database=db_name,host=host, user="cprvalidation",password=d_pass,port=d_port)
            except Exception as e:
                print(e)
                sys.exit()
            current_time = datetime.datetime.utcnow() #Timestamp
            select = """
                        SELECT * FROM {0}.status
                        WHERE package_id = %s AND excepted IS NOT NULL;
                    """
            insert = """
                        UPDATE {0}.status
                        SET status='invalid', last_checked= %s,cpr_number=%s
                        WHERE resource_id= %s
                        returning *
            ;"""


            cur = conn.cursor()
            cur.execute(select.format(db_name),[resource[0]])
            if(len(cur.fetchall()) > 0 ): #There was an exception made for this resource
                print(("Exception was made for package with id: %s ignoring." % resource[0]))
                return

            cur.execute(insert.format(db_name), [current_time,iscpr[1],id])
            conn.commit()
            conn.close()

            try:
                print("Making dataset private")
                package_id = resource[0]
                package = get_action('package_show')({},{'id': package_id})
            except Exception as e:
                print("Could not show package")
                print((e.message))
                sys.exit(1)
            try:
                if(package["private"] == True): #If the dataset is already private, we do not need to send an email otherwise we spam
                    return
                package["private"] = True
                get_action('package_update')({},package)
                print(("Made dataset with package id: " + package_id + " private as it contains CPR data. Either add an exception or remove it from the site"))
                print("When an exception has been made or data altered, kindly mark data as public again")

                recipient = config.get('ckan.cprvalidation.email', None)
                subject = "CPR fundet i datasæt: %s" % resource[0]
                body = "CPR data er fundet i datasættet med id: %s specifikt resourcen med id: %s \n Data er gjort privat, tjek data igennem og " \
                            "publicer igen eller tilføj en exception hvis du mener data ikke indeholder CPR og kan stå" \
                            " inde for dette." % (resource[0],id)
                try:
                    process = subprocess.Popen(['mail', '-s', subject,'-r',"teknik@opendata.dk", recipient],
                                                        stdin=subprocess.PIPE)
                except Exception as error:
                    print(error)
                process.communicate(body)

            except Exception as e:
                print("Could not update package")
                print((e.message))
                sys.exit(1)

def scanDB():
    d_port = config.get('ckan.cprvalidation.postgres_port', None)
    d_pass = config.get('ckan.cprvalidation.cprvalidation_password', None)
    db_name = config.get('ckan.cprvalidation.cprvalidation_db', None)

    try:
        host = config.get('POSTGRES_HOST', 'db')
        conn = psycopg2.connect(database=db_name, host=host, user="cprvalidation", password=d_pass,
                                port=d_port)
    except Exception as e:
        print(e)
        sys.exit()

    # TODO: PDF is really slow, so we need to fix that, removed for now
    select = """
                   SELECT * FROM """ + db_name + """.status
                   WHERE format = ANY('{csv,xlsx,json,geojson,ods,docx}')
                   AND (last_updated::timestamp >= last_checked::timestamp OR last_checked IS NULL)
                   AND (url_type IS NOT NULL OR datastore_active = 'true')
                   AND excepted IS NULL;
       """
    print("Scanning for updates...")
    cur = conn.cursor()
    # Get the datasets we have to validate
    cur.execute(select)
    tmp_return = cur.fetchall()
    conn.commit()
    conn.close()

    # Return them
    print(("Found %d updated resources to validate \n" % len(tmp_return)))
    return tmp_return

def updateSchema(resources):
    #Connect to the database
    d_port = config.get('ckan.cprvalidation.postgres_port', None)
    d_pass = config.get('ckan.cprvalidation.cprvalidation_password', None)
    db_name = config.get('ckan.cprvalidation.cprvalidation_db', None)

    try:
        host = config.get('POSTGRES_HOST', 'db')
        conn = psycopg2.connect(database=db_name,host=host, user="cprvalidation",password=d_pass,port=d_port)
    except Exception as e:
        print(e)
        sys.exit()

    # Fetch all resources from the database
    print("Looking for new resources..")
    cur = conn.cursor()
    print(db_name)
    cur.execute("""SELECT resource_id, last_updated FROM {0}.status;
        """.format(db_name))
    database_resources = cur.fetchall()
    # These are new resources
    difference_insert = list(set([str(r['id']) for r in resources]) - set(r[0] for r in database_resources))
    difference_update = list(set([(str(r['metadata_modified']).replace("T"," ")) for r in resources]) - set(str(r[1]) for r in database_resources))

    insert = """
                INSERT INTO {0}.status values %s
                ON CONFLICT (resource_id) DO
                  UPDATE SET last_updated = %s
                returning *
            ;"""
    update = """
                    UPDATE {0}.status SET last_updated = %s
                    WHERE resource_id = %s
                    returning *
                ;"""

    '''       Overview of the table order
     (
              package_id character varying NOT NULL,
              resource_id character varying NOT NULL,
              status character varying, -- valid, invalid, pending
              format character varying NOT NULL,
              url character varying,
              url_type character varying,
              datastore_active character varying,
              last_checked character varying,
              last_updated character varying,
              excepted BOOLEAN
              CONSTRAINT status_pkey PRIMARY KEY (resource_id)
            )
    '''
    #For each new resource, add them to the schema and set their status to pending
    count = 0
    for id in difference_insert:
        count += 1
        dict = find(resources,"id",id)
        i = (dict["package_id"],
             dict["id"],
             "pending",
             str(dict["format"]).lower(),
             dict["url"],
             dict["url_type"],
             dict["datastore_active"],
             None,
             dict["metadata_modified"],
             )
        u = dict["metadata_modified"]
        cur.execute(insert.format(db_name), (i,u))
    print(("Inserted %d new resources to the database \n" % count))

    # # #
    # Update the information for last_updated
    # # #
    count = 0
    for date in difference_update:
        #Multiple resources can share the same metadata_modified, so check them all
        dicts = findall(resources, "metadata_modified", date.replace(" ", "T"))
        for dict in dicts:
            count += 1
            i = dict["metadata_modified"]
            try:
                cur.execute(update.format(db_name), (i, dict["id"]))
            except Exception as e:
                print((e.message))
    print(("Updated %d new resources to the database \n" % count))
    try:
        conn.commit()
        conn.close()
    except Exception as e:
        print((e.message))

def getAllResources():
    #We don't check private resources.
    #If you want to change this, the logic should be changed, as metadata_modified will update when we find a CPR number
    #resulting in an infinite scan
    response = get_action('package_search')({}, {'rows': 1000000, 'include_private':True})
    local_data = response['results']

    resources = []
    dates = []
    for package in local_data:
        for resource in package["resources"]:
            resource["metadata_modified"] = package["metadata_modified"]
            dates.append(package["metadata_modified"])
            resources.append(resource)

    if dates:
        print((sorted(dates,reverse=True)[0]))

    return resources

# Simple function that helps us find a value in a list of dicts
def find(lst, key, value):
    for i, dic in enumerate(lst):
        if dic[key] == value:
            return dic
    return None

# Simple function that finds all values in a list of dicts
def findall(lst,key,value):
    found = []
    for i, dic in enumerate(lst):
        if dic[key] == value:
            found.append(dic)
    if(len(found) != 0):
        return found
    else:
        return []

def validcpr(file_string):
    monthList = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    magic = [4, 3, 2, 7, 6, 5, 4, 3, 2, 1]

    # REGEX Explanation:
    # A or B (A | B)
    # Case A:
    # 6 digits followed by a dash followed by 4 digits
    # Word boundary \b at either end so we wont submatch

    # Case B:
    # 10 digits surrounded by word boundary so we wont submatch
    pattern = re.compile(r"\b(?![\*\.\-\:])\d{6}[-]\d{4}\b(?![\*\.\-\:])|\b(?<![\*\.\-\:])\d{10}\b(?![\*\.\-\:])")

    # Find all match in the file_string
    m = re.findall(pattern, file_string)

    if (len(m) == 0):
        return (False, "No CPR Detected")

    for cpr in m:
        # Since we use word boundaries, remove the trailing spaces
        cpr = cpr.replace(" ", "")
        cpr = cpr.replace("-", "")

        if len(cpr) != 10:
            iscpr = (False, "No CPR Detected")
        elif not cpr.isdigit():
            iscpr = (False, "No CPR Detected")
        elif int(cpr[0:2]) > 31 or int(cpr[0:2]) <= 0:
            iscpr = (False, "No CPR Detected")
        elif int(cpr[2:4]) > 12 or int(cpr[2:4]) <= 0:
            iscpr = (False, "No CPR Detected")
        elif monthList[int(cpr[2:4]) - 1] < int(cpr[0:2]):
            iscpr = (False, "No CPR Detected")
        else:  # All the checks "fail" meaning we might have a CPR number
            sum = 0
            # Do the mod 11 method
            for i in range(0, len(cpr)):
                sum = sum + int(cpr[i:i + 1]) * magic[i]  # Use the magic list to find CPR match
            if sum % 11 == 0:  # If we get mod 11 we have a CPR number
                # Might as well return as we only need 1 match
                return (True, str(cpr))
            else:
                iscpr = (False, "No CPR Detected")

    return iscpr
