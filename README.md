# ckanext-cprvalidation
Validates resources for the Danish national open data platform
http://portal.opendata.dk

### **Upgrading to CKAN 2.11 & Python 3**  

This extension has been upgraded from CKAN **v2.6** to **v2.11**, replacing deprecated components and ensuring compatibility with **Python 3**.  

### **Key Changes in the Upgrade:**  
✅ **Replaced Pylons routes** → **Flask Blueprints** for routing  
✅ **Converted old Paster commands** → **Click CLI commands**  
✅ **Updated database connection handling**  
✅ **Improved logging and error handling**  
✅ **Refactored file processing methods** (DOCX, ODS, PDF, etc.)  

---

### **New Features & Changes**  

### **1. Flask Blueprint for Downloading CPR Report**  
The old `IRoutes` mapping has been replaced with a **Flask Blueprint**, making `/download/cprreport` available as an endpoint.  

### **2. Migrating CLI Commands to Click**  
Old Paster commands have been replaced with **Click-based CLI commands**, improving usability and integration with CKAN 2.11.  

- **`cpr-initdb`**: Initializes the CPR validation database.  
- **`cpr-scan`**: Scans CKAN resources for validation.  
- **`cpr-addexception`**: Adds exceptions to the CPR validation database.  

Run these commands using:  
```bash
ckan -c /etc/ckan/default/ckan.ini cpr-initdb
```

### **3. Updated Resource Validation**  
- Supports multiple file formats: **CSV, XLSX, DOCX, PDF, ODS, JSON**  
- Improved handling of file downloads and processing  
- Updated error handling for invalid or missing resources  

### **4. Updated Database Handling**  
- The `cprvalidation` database role is created dynamically during initialization.  
- Improved PostgreSQL connection handling.  
- Refactored schema and table creation for better compatibility with modern CKAN versions.  

### **5. Improved Logging & Error Handling**  
- More detailed error messages for debugging.  
- Proper logging of database connection failures.  
- Enhanced file-processing error detection.  

### **6. Compatibility with CKAN 2.11**  
- Updated schema overrides to work with CKAN’s newer dataset schema structure.  
- Integrated the validation process with CKAN’s API more efficiently.  


## Installation guide:

### Activate virtualenv
```
source /usr/lib/ckan/default/bin/activate
cd /usr/lib/ckan/default/src
git clone git@github.com:NicolaiMogensen/ckanext-cprvalidation.git
cd ckanext-cprvalidation
```

### Install Extension
```
python setup.py develop

```
### Enable plugin in configuration
```
 sudo nano /etc/ckan/default/production.ini
 ckan.plugins = datastore ... cprvalidation
```
### Add database settings to production.ini
```
ckan.cprvalidation.postgres_password = "Postgres password here"
ckan.cprvalidation.cprvalidation_db = "Database name for validation"
ckan.cprvalidation.cprvalidation_password = "Password you will be using for the dedicated user"
ckan.cprvalidation.postgres_port = "The port postgres is running, default is 5432"
ckan.cprvalidation.apikey = "A CKAN API key that can view private resources"
ckan.cprvalidation.email = "Email that the OS should send report to. "mail" must be setup"
```

### Create user "cprvalidation"
```
sudo -u postgres psql
CREATE ROLE cprvalidation WITH LOGIN ENCRYPTED PASSWORD 'xxx';
```
NB: The password must be the same as the one entered in your config

## Usage

### Init the database
```bash
paster --plugin=ckanext-cprvalidation validation initdb --config=/etc/ckan/default/production.ini
```
Or you can use
```bash
ckan -c /etc/ckan/default/ckan.ini cpr-initdb
```

### Setup a CRON job to scan at regular intervals.
```
*/30 * * * * cd /usr/lib/ckan/default/src/ckanext-cprvalidation && /usr/lib/ckan/default/bin/python /usr/lib/ckan/default/bin/paster plugin=ckanext-cprvalidation validation scan --config=/etc/ckan/default/production.ini
```
### Add exceptions to the database
```
paster --plugin=ckanext-cprvalidation validation addexception "Package_id" --config=/etc/ckan/default/production.ini
```
