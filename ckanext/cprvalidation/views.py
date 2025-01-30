import psycopg2
import logging
from io import StringIO 
# from ckan.lib.cli import parse_db_config
from ckan.common import config

from flask import Blueprint, Response, abort

log = logging.getLogger(__name__)

cpr = Blueprint("cpr", __name__)

def download():
        port = config.get('ckan.cprvalidation.postgres_port', None)
        password = config.get('ckan.cprvalidation.cprvalidation_password',None)
        db_name = config.get('ckan.cprvalidation.cprvalidation_db',None)
        host = config.get('POSTGRES_HOST', 'db')
        if port != None and password != None:
            try:
                conn = psycopg2.connect(database=db_name, host=host, user="cprvalidation", password=password,
                                    port=port)
            except Exception as e:
                log.warning(e)
                abort(500, description="Database connect error")
        else:
            log.warning("Config not setup properly! Missing either postgres_port or cprvalidation_password")
            abort(500, description="Improper config setup")
            

        select = """COPY (SELECT * FROM {0}.status) to STDOUT WITH CSV HEADER"""
        cur = conn.cursor()

        #Instead of using an actual file, we use a file-like string buffer
        text_stream = StringIO()

        cur.copy_expert(select.format(db_name),text_stream)
        output = text_stream.getvalue()

        #Cleanup after ourselves
        text_stream.close()
        conn.commit()
        conn.close()

        response = Response(output, mimetype='text/csv;charset=utf-8')
        response.headers['Content-Disposition'] = 'attachment; filename="cpr_report.csv"'
        return response

cpr.add_url_rule('/download/cprreport', methods=["GET"], view_func=download)