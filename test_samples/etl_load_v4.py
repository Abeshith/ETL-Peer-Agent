import yaml
import argparse
from pathlib import Path
from os import listdir
from os.path import isfile, join

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.absolute()))

from utils.snowflake.ay_loader import AYSnowflakeLoader
from utils.ay_akv import AyAzureKeyVault

parser = argparse.ArgumentParser()
parser.add_argument('--env', dest='env', type=str, help='Project Environment')
# parser.add_argument('--run_date', dest='run_date', type=str, help='Run Date')
args = parser.parse_args()

if args.env.lower() not in ['dev', 'prd']:
    raise Exception(f"{args.env.lower()} is not a valid environment. Valid options are only dev, uat, and prod.")

env = args.env.lower()

## Read YAML file
with open(f'{Path(__file__).parent.absolute()}/config.yml', 'r') as stream:
    etl_configuration = yaml.safe_load(stream)[env]

azureKV = AyAzureKeyVault()
key_vault_name = etl_configuration['key_vault']
vault_uri = f'https://{key_vault_name}.vault.azure.net/'


if __name__ == '__main__':
    for snowflake_configuration in etl_configuration['snowflake']:
        
        snowflake_account = snowflake_configuration['account']
        snowflake_user = snowflake_configuration['user']
        snowflake_role = snowflake_configuration['role']
        snowflake_warehouse = snowflake_configuration['warehouse']
        snowflake_database = snowflake_configuration['database']
        snowflake_schema = snowflake_configuration['schema']

        snowflake_private_key = azureKV.get_secret(vault_uri, snowflake_configuration['private_key']['secret_name']).encode("utf-8")

        with AYSnowflakeLoader(
            snowflake_account = snowflake_account,
            snowflake_user = snowflake_user,
            snowflake_role = snowflake_role,
            snowflake_private_key = snowflake_private_key,
            snowflake_warehouse = snowflake_warehouse,
            snowflake_database = snowflake_database,
            snowflake_schema = snowflake_schema
        ) as snowflake_conn:
            
            # Generate CALL statements for CSV files in stage
            # Using hardcoded file from the stage instead of DIRECTORY function (not supported)
            print("Preparing stored procedure calls for CSV files in stage...")
            
            # For v4, we'll call the procedures directly with known file pattern
            sql_statements = [
                "CALL AGS_RAW.PUBLIC.LOAD_METRICS_CSV('AGS_RAW_STAGE','ags_metrics_001.csv','ID');",
            ]

            # results = self.snowflake_connector.cursor().execute(sql_command, num_statements=num_statements).fetchall()
            # return results
            print("Executing the following stored procedure calls:")
            for stmt in sql_statements:
                print(stmt)
                result = snowflake_conn.run_query(stmt)
                print(result)
                print("-" * 100)
            
            print("Creating Statistics Table")
            print("-" * 100)

            STATISTICS_SQL = "CALL AGS_RAW.PUBLIC.LOAD_STATISTICS_SQL('AGS_RAW_STAGE');"
            print(STATISTICS_SQL)
            result = snowflake_conn.run_query(STATISTICS_SQL)
            print(result)
            print("-" * 100)

            print("Creating Dictionary Table")
            print("-" * 100)

            DICTIONARY_SQL = "CALL AGS_RAW.PUBLIC.LOAD_DICTIONARY_SQL('AGS_RAW_STAGE');"
            print(DICTIONARY_SQL)
            result = snowflake_conn.run_query(DICTIONARY_SQL)
            print(result)
            print("-" * 100)
