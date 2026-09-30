import argparse, yaml, logging, sys
from pathlib import Path
from os import listdir
from os.path import isfile, join

from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.absolute()))

from utils.snowflake.ay_loader import AYSnowflakeLoader
from utils.ay_akv import AyAzureKeyVault

logger = logging.getLogger(__name__)
logging.basicConfig(format="%(levelname)s:%(name)s:%(message)s", level=logging.INFO)

parser = argparse.ArgumentParser()
parser.add_argument('--env', dest='env', type=str, help='Project Environment')
args = parser.parse_args()

if args.env.lower() not in ['dev_v2', 'dev_v2_new']:
    raise Exception(f"{args.env.lower()} is not a valid environment. Valid options are dev_v2 or dev_v2_new.")

env = args.env.lower()
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
            
            for dml_configuration in snowflake_configuration['tables_to_copy']:
                print(f"Running Table Create Statement for {snowflake_database}.{snowflake_schema}.{dml_configuration['filename']}.")
                COPY_SQL = open(f"{Path(__file__).parent.absolute()}/{dml_configuration['filename']}").read().format(
                    DATABASE=snowflake_database,
                    SCHEMA=snowflake_schema
                )
                
                copy_result = snowflake_conn.run_query(COPY_SQL)
                logger.info(f"{copy_result[0][0]}")
            
            insert_parcel_uuid = (f"""
                                    INSERT INTO ETL_RAW.LIGHTBOX.PARCELS_UUID (PARCEL_ID, PARCEL_VENDOR_ID)
                                    SELECT UUID_STRING(), PARCEL_LID 
                                    FROM EXT_LIGHTBOX.PROFESSIONAL.PARCELS N 
                                    WHERE NOT EXISTS (SELECT 1 
                                                      FROM ETL_RAW.LIGHTBOX.PARCELS_UUID O
                                                      WHERE O.PARCEL_VENDOR_ID = N.PARCEL_LID
                                                     )
                                """)
            cursor = snowflake_conn.run_query(insert_parcel_uuid)

            parcel_count = cursor[0][0]
            logger.info("Totally %s new parcel ID's are inserted.", parcel_count)

            # This is used to insert new parcels with assessment_ID (UUID)
            insert_assessment_uuid = (f"""
                                    INSERT INTO ETL_RAW.LIGHTBOX.ASSESSMENTS_UUID (ASSESSMENT_ID, ASSESSMENT_VENDOR_ID)
                                    SELECT UUID_STRING(), ASSESSMENT_LID 
                                    FROM EXT_LIGHTBOX.PROFESSIONAL.ASSESSMENTS N 
                                    WHERE NOT EXISTS (SELECT 1 
                                                      FROM ETL_RAW.LIGHTBOX.ASSESSMENTS_UUID O
                                                      WHERE O.ASSESSMENT_VENDOR_ID = N.ASSESSMENT_LID
                                                     )
                                """)
            cursor = snowflake_conn.run_query(insert_assessment_uuid)

            assess_count = cursor[0][0]
            logger.info("Totally %s new assessment ID's are inserted.", assess_count)
            
            root_dir = Path(__file__).parent.absolute()
            if "shares" in snowflake_configuration:
                share_files = [f for f in listdir(Path(join(root_dir, 'ddl', 'shares'))) if f.endswith('.sql')]
                for share_file in share_files:
                    for shares_configuration in snowflake_configuration['shares']:
                        print(f"Running Share DDL Statement {share_file} to Share {shares_configuration['name']} in {shares_configuration['database']}.{shares_configuration['schema']}.")
                        CREATE_SHARE_SQL = open(Path(join(root_dir, 'ddl', 'shares', share_file))).read().format(
                            DATABASE=snowflake_database,
                            SCHEMA=snowflake_schema,
                            SHARED_DATABASE=shares_configuration['database'],
                            SHARED_SCHEMA=shares_configuration['schema'],
                            SHARE_NAME=shares_configuration['name']
                        )
                        create_response = snowflake_conn.run_query(CREATE_SHARE_SQL, num_statements=5)
                        logger.info(create_response[0][0])
