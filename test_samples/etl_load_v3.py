from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict

import snowflake.connector
import yaml
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization

sys.path.append(str(Path(__file__).parent.parent.absolute()))
from utils.ay_akv import AyAzureKeyVault  # noqa: E402

logger = logging.getLogger(__name__)
logging.basicConfig(format="%(levelname)s:%(name)s:%(message)s", level=logging.INFO)


def get_key(cfg: Dict[str, Any], key: str, default=None):
    cur: Any = cfg
    for p in key.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur if cur is not None else default


def required_key(cfg: Dict[str, Any], key: str) -> Any:
    v = get_key(cfg, key)
    if v is None or (isinstance(v, str) and not v.strip()):
        raise ValueError(f"Missing/empty config key: {key}")
    return v


def storage_account(conn_str: str) -> str:
    m = re.search(r"AccountName=([^;]+)", conn_str)
    if not m:
        raise ValueError("Could not parse AccountName from Azure connection string")
    return m.group(1)


def pem_to_der(pem_text: str) -> bytes:
    k = serialization.load_pem_private_key(
        pem_text.encode("utf-8"), password=None, backend=default_backend()
    )
    return k.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def qident(x: str) -> str:
    x = (x or "").replace('"', '""')
    return f'"{x}"'


def qname(*parts: str) -> str:
    return ".".join(qident(p) for p in parts if p and str(p).strip())


def norm(p: str) -> str:
    p = (p or "").strip().lstrip("/").replace("\\", "/")
    while "//" in p:
        p = p.replace("//", "/")
    return p


def pattern(base_prefix: str, ext_regex: str) -> str:
    base = norm(base_prefix).rstrip("/")
    return rf"^{re.escape(base)}.*/.*{ext_regex}$"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default="dev_v3", help="dev_v3")
    ap.add_argument("--run_date", default=None, help="Airflow date (YYYY-MM-DD)")
    ap.add_argument("--force_copy", action="store_true", help="Force Snowflake COPY")
    args = ap.parse_args()

    cfg_path = Path(__file__).with_name("config.yml")
    all_cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    cfg: Dict[str, Any] = all_cfg[(args.env or "dev").lower().strip()]

    vault = f"https://{required_key(cfg, 'key_vault')}.vault.azure.net/"
    kv = AyAzureKeyVault()
    conn_str = kv.get_secret(vault, required_key(cfg, "blob_storage.secret_name"))
    storage_acct = storage_account(conn_str)

    container = required_key(cfg, "blob_storage.container_name")
    base_prefix = required_key(cfg, "blob_storage.base_prefix")
    target_prefix = required_key(cfg, "blob_storage.target_prefix")
    ext_regex = get_key(cfg, "load.pattern_ext", r"\.geojson")

    stage_url = f"azure://{storage_acct}.blob.core.windows.net/{container}"
    pat = pattern(str(base_prefix), str(ext_regex))

    logger.info("Stage URL: %s", stage_url)
    logger.info("COPY PATTERN: %s", pat)

    for sf in required_key(cfg, "snowflake"):
        account = required_key(sf, "account")
        database = required_key(sf, "database")
        schema = required_key(sf, "schema")
        stage = required_key(sf, "stage")
        user = required_key(sf, "user")
        role = required_key(sf, "role")
        warehouse = required_key(sf, "warehouse")

        storage_integration = get_key(
            sf, "storage_integration", "ETL_PRODUCT_INTEGRATION_LND"
        )
        term_target_table = get_key(sf, "term_target_table")
        pipe_target_table = get_key(sf, "pipe_target_table")
        load_mode = (get_key(sf, "load_mode", "append") or "append").lower().strip()

        pk_pem = kv.get_secret(vault, required_key(sf, "private_key.secret_name"))
        pk_der = pem_to_der(pk_pem)

        force_copy = bool(args.force_copy) or load_mode in {"truncate", "replace"}

        conn = snowflake.connector.connect(
            account=account,
            user=user,
            role=role,
            warehouse=warehouse,
            database=database,
            schema=schema,
            private_key=pk_der,
        )
        conn.autocommit(True)
        cur = conn.cursor()

        try:
            logger.info("Using existing stage: %s", stage)

            cur.execute(f"""
                CREATE OR REPLACE TABLE {qname(database, schema, term_target_table)}
                (
                    PROJECT_ID STRING NOT NULL,
                    UNIT_ID STRING NOT NULL,
                    COUNTRY_AREA STRING,
                    WIKI STRING,
                    TERMINAL_NAME STRING,
                    UNIT_NAME STRING,
                    FACILITY_TYPE STRING,
                    FUEL STRING,
                    STATUS STRING,
                    OTHER_NAMES STRING,
                    LOCAL_NAMES STRING,
                    LANGUAGE STRING,
                    OWNER STRING,
                    OWNER_GEM_ENTITY_ID STRING,
                    PARENT STRING,
                    PARENT_GEM_ENTITY_ID STRING,
                    PARENTHQ_COUNTRY STRING,
                    OPERATOR STRING,
                    CAPACITY STRING,
                    CAPACITY_UNITS STRING,
                    CAPACITY_IN_MTPA STRING,
                    CAPACITY_INBCM_Y STRING,
                    TOT_IMPORT_LNG_TERMINAL_CAPACITY_IN_MTPA STRING,
                    TOT_IMPORT_LNG_TERMINAL_CAPACITY_IN_BCM_Y STRING,
                    TOT_EXPORT_LNG_TERMINAL_CAPACITY_IN_MTPA STRING,
                    TOT_EXPORT_LNG_TERMINAL_CAPACITY_IN_BCM_Y STRING,
                    PROPOSAL_YEAR STRING,
                    PROPOSAL_MONTH STRING,
                    CONSTRUCTION_YEAR STRING,
                    CONSTRUCTION_MONTH STRING,
                    ORIGINAL_PLANNED_START_YEAR STRING,
                    LATEST_PLANNED_START_YEAR STRING,
                    ACTUAL_START_YEAR STRING,
                    ACTUAL_START_MONTH STRING,
                    ACTUAL_START_YEAR2 STRING,
                    ACTUAL_START_YEAR3 STRING,
                    SHELVED_YEAR STRING,
                    CANCELLED_YEAR STRING,
                    STOP_YEAR STRING,
                    PLANNED_STOP_YEAR STRING,
                    SHELVED_CANCELLED_STATUS_TYPE STRING,
                    TEMP_FACILITY STRING,
                    IMPORT_EXPORT_ONLY STRING,
                    LOCATION STRING,
                    REGION STRING,
                    SUB_REGION STRING,
                    PREFECTURE_DISTRICT STRING,
                    STATE_PROVINCE STRING,
                    LATITUDE STRING,
                    LONGITUDE STRING,
                    ACCURACY STRING,
                    ASSOCIATED_TERMINALS STRING,
                    POWER_PLANTS_SUPPLIED STRING,
                    COST STRING,
                    COST_UNITS STRING,
                    COST_USD STRING,
                    TOT_KNOWN_TERMINAL_COSTS_USD STRING,
                    FID_STATUS STRING,
                    FID_YEAR STRING,
                    FINANCING STRING,
                    OFFSHORE STRING,
                    FLOATING STRING,
                    FLOATING_VESSEL_NAME STRING,
                    VESSEL_OWNER STRING,
                    VESSEL_OPERATOR STRING,
                    LH2 STRING,
                    NH3 STRING,
                    SYNTHETIC_LNG STRING,
                    RETROFIT_PROPOSED STRING,
                    ALTFUEL_PRELIMAGREEMENT STRING,
                    ALTFUEL_CALL_MARKET_INTEREST STRING,
                    CCS STRING,
                    LAST_UPDATED STRING,
                    GEOMETRY GEOGRAPHY,
                    PRIMARY KEY (PROJECT_ID, UNIT_ID)
                )
                """)

            cur.execute(f"""
                INSERT INTO {qname(database, schema, term_target_table)}
                (
                    PROJECT_ID, UNIT_ID, COUNTRY_AREA, WIKI, TERMINAL_NAME, UNIT_NAME,
                    FACILITY_TYPE, FUEL, STATUS, OTHER_NAMES, LOCAL_NAMES, LANGUAGE,
                    OWNER, OWNER_GEM_ENTITY_ID, PARENT, PARENT_GEM_ENTITY_ID,
                    PARENTHQ_COUNTRY, OPERATOR, CAPACITY, CAPACITY_UNITS,
                    CAPACITY_IN_MTPA, CAPACITY_INBCM_Y,
                    TOT_IMPORT_LNG_TERMINAL_CAPACITY_IN_MTPA,
                    TOT_IMPORT_LNG_TERMINAL_CAPACITY_IN_BCM_Y,
                    TOT_EXPORT_LNG_TERMINAL_CAPACITY_IN_MTPA,
                    TOT_EXPORT_LNG_TERMINAL_CAPACITY_IN_BCM_Y,
                    PROPOSAL_YEAR, PROPOSAL_MONTH, CONSTRUCTION_YEAR, CONSTRUCTION_MONTH,
                    ORIGINAL_PLANNED_START_YEAR, LATEST_PLANNED_START_YEAR,
                    ACTUAL_START_YEAR, ACTUAL_START_MONTH, ACTUAL_START_YEAR2,
                    ACTUAL_START_YEAR3, SHELVED_YEAR, CANCELLED_YEAR, STOP_YEAR,
                    PLANNED_STOP_YEAR, SHELVED_CANCELLED_STATUS_TYPE, TEMP_FACILITY,
                    IMPORT_EXPORT_ONLY, LOCATION, REGION, SUB_REGION,
                    PREFECTURE_DISTRICT, STATE_PROVINCE, LATITUDE, LONGITUDE, ACCURACY,
                    ASSOCIATED_TERMINALS, POWER_PLANTS_SUPPLIED, COST, COST_UNITS,
                    COST_USD, TOT_KNOWN_TERMINAL_COSTS_USD, FID_STATUS, FID_YEAR,
                    FINANCING, OFFSHORE, FLOATING, FLOATING_VESSEL_NAME, VESSEL_OWNER,
                    VESSEL_OPERATOR, LH2, NH3, SYNTHETIC_LNG, RETROFIT_PROPOSED,
                    ALTFUEL_PRELIMAGREEMENT, ALTFUEL_CALL_MARKET_INTEREST, CCS,
                    LAST_UPDATED, GEOMETRY
                )
                SELECT
                f.value:properties:"ProjectID"::STRING,
                f.value:properties:"UnitID"::STRING,
                f.value:properties:"Country/Area"::STRING,
                f.value:properties:"Wiki"::STRING,
                f.value:properties:"TerminalName"::STRING,
                f.value:properties:"UnitName"::STRING,
                f.value:properties:"FacilityType"::STRING,
                f.value:properties:"Fuel"::STRING,
                f.value:properties:"Status"::STRING,
                f.value:properties:"OtherNames"::STRING,
                f.value:properties:"LocalNames"::STRING,
                f.value:properties:"Language"::STRING,
                f.value:properties:"Owner"::STRING,
                f.value:properties:"Owner GEM Entity ID"::STRING,
                f.value:properties:"Parent"::STRING,
                f.value:properties:"Parent GEM Entity ID"::STRING,
                f.value:properties:"ParentHQCountry"::STRING,
                f.value:properties:"Operator"::STRING,
                f.value:properties:"Capacity"::STRING,
                f.value:properties:"CapacityUnits"::STRING,
                f.value:properties:"CapacityinMtpa"::STRING,
                f.value:properties:"CapacityinBcm/y"::STRING,
                f.value:properties:"TotImportLNGTerminalCapacityinMtpa"::STRING,
                f.value:properties:"TotImportLNGTerminalCapacityinBcm/y"::STRING,
                f.value:properties:"TotExportLNGTerminalCapacityinMtpa"::STRING,
                f.value:properties:"TotExportLNGTerminalCapacityinBcm/y"::STRING,
                f.value:properties:"ProposalYear"::STRING,
                f.value:properties:"ProposalMonth"::STRING,
                f.value:properties:"ConstructionYear"::STRING,
                f.value:properties:"ConstructionMonth"::STRING,
                f.value:properties:"OriginalPlannedStartYear"::STRING,
                f.value:properties:"LatestPlannedStartYear"::STRING,
                f.value:properties:"ActualStartYear"::STRING,
                f.value:properties:"ActualStartMonth"::STRING,
                f.value:properties:"ActualStartYear2"::STRING,
                f.value:properties:"ActualStartYear3"::STRING,
                f.value:properties:"ShelvedYear"::STRING,
                f.value:properties:"CancelledYear"::STRING,
                f.value:properties:"StopYear"::STRING,
                f.value:properties:"PlannedStopYear"::STRING,
                f.value:properties:"ShelvedCancelledStatusType"::STRING,
                f.value:properties:"TempFacility"::STRING,
                f.value:properties:"ImportExportOnly"::STRING,
                f.value:properties:"Location"::STRING,
                f.value:properties:"Region"::STRING,
                f.value:properties:"SubRegion"::STRING,
                f.value:properties:"Prefecture/District"::STRING,
                f.value:properties:"State/Province"::STRING,
                f.value:properties:"Latitude"::STRING,
                f.value:properties:"Longitude"::STRING,
                f.value:properties:"Accuracy"::STRING,
                f.value:properties:"AssociatedTerminals"::STRING,
                f.value:properties:"PowerPlantsSupplied"::STRING,
                f.value:properties:"Cost"::STRING,
                f.value:properties:"CostUnits"::STRING,
                f.value:properties:"CostUSD"::STRING,
                f.value:properties:"TotKnownTerminalCostsUSD"::STRING,
                f.value:properties:"FIDStatus"::STRING,
                f.value:properties:"FIDYear"::STRING,
                f.value:properties:"Financing"::STRING,
                f.value:properties:"Offshore"::STRING,
                f.value:properties:"Floating"::STRING,
                f.value:properties:"FloatingVesselName"::STRING,
                f.value:properties:"VesselOwner"::STRING,
                f.value:properties:"VesselOperator"::STRING,
                f.value:properties:"LH2"::STRING,
                f.value:properties:"NH3"::STRING,
                f.value:properties:"SyntheticLNG"::STRING,
                f.value:properties:"RetrofitProposed"::STRING,
                f.value:properties:"AltFuelPrelimAgreement"::STRING,
                f.value:properties:"AltFuelCallMarketInterest"::STRING,
                f.value:properties:"CCS"::STRING,
                f.value:properties:"LastUpdated"::STRING,
                TO_GEOGRAPHY(f.value:geometry)
                FROM @{database}.{schema}.GLOBAL_ENERGY_MONITOR_STAGE
                    (FILE_FORMAT => {database}.{schema}.GEOJSON_FF) x,
                    LATERAL FLATTEN(input => x.$1:features) f
                WHERE METADATA$FILENAME ILIKE '%Terminal%.geojson'
                """)

            cur.execute(f"""
                CREATE OR REPLACE TABLE {qname(database, schema, pipe_target_table)}
                (
                    PROJECT_ID STRING NOT NULL,
                    PIPELINE_NAME STRING,
                    SEGMENT_NAME STRING,
                    WIKI STRING,
                    STATUS STRING,
                    LAST_UPDATED STRING,
                    FUEL STRING,
                    OTHER_ENGLISH_NAMES STRING,
                    OTHER_LANGUAGE_PRIMARY_PIPELINE_NAME STRING,
                    OTHER_LANGUAGE_SEGMENT_NAME STRING,
                    COUNTRIES_OR_AREAS STRING,
                    OWNER STRING,
                    PARENT_ENTITY_IDS STRING,
                    PARENT STRING,
                    START_YEAR1 STRING,
                    START_YEAR2 STRING,
                    START_YEAR3 STRING,
                    SHELVED_YEAR STRING,
                    CANCELLED_YEAR STRING,
                    STOP_YEAR STRING,
                    CAPACITY STRING,
                    CAPACITY_UNITS STRING,
                    CAPACITY_BCM_Y STRING,
                    CAPACITY_BOED STRING,
                    LENGTH_KNOWN_KM STRING,
                    LENGTH_ESTIMATE_KM STRING,
                    LENGTH_MERGED_KM STRING,
                    DIAMETER STRING,
                    DIAMETER_UNITS STRING,
                    FUEL_SOURCE STRING,
                    START_LOCATION STRING,
                    START_PREFECTURE_DISTRICT STRING,
                    START_STATE_PROVINCE STRING,
                    START_COUNTRY_OR_AREA STRING,
                    START_REGION STRING,
                    START_SUB_REGION STRING,
                    END_LOCATION STRING,
                    END_PREFECTURE_DISTRICT STRING,
                    END_STATE_PROVINCE STRING,
                    END_COUNTRY_OR_AREA STRING,
                    END_REGION STRING,
                    END_SUB_REGION STRING,
                    PROJECT_LEVEL_COST STRING,
                    PROJECT_LEVEL_COST_UNITS STRING,
                    COST_USD STRING,
                    FID_STATUS STRING,
                    FID_YEAR STRING,
                    PCI3 STRING,
                    PCI4 STRING,
                    PCI5 STRING,
                    PCI6 STRING,
                    ROUTE_ACCURACY STRING,
                    ROUTE_TYPE STRING,
                    GEOMETRY STRING,
                    PRIMARY KEY (PROJECT_ID)
                )
            """)

            cur.execute(f"""
                INSERT INTO {qname(database, schema, pipe_target_table)}
                (
                    PROJECT_ID, PIPELINE_NAME, SEGMENT_NAME, WIKI, STATUS, LAST_UPDATED,
                    FUEL, OTHER_ENGLISH_NAMES, OTHER_LANGUAGE_PRIMARY_PIPELINE_NAME,
                    OTHER_LANGUAGE_SEGMENT_NAME, COUNTRIES_OR_AREAS, OWNER,
                    PARENT_ENTITY_IDS, PARENT, START_YEAR1, START_YEAR2, START_YEAR3,
                    SHELVED_YEAR, CANCELLED_YEAR, STOP_YEAR, CAPACITY, CAPACITY_UNITS,
                    CAPACITY_BCM_Y, CAPACITY_BOED, LENGTH_KNOWN_KM, LENGTH_ESTIMATE_KM,
                    LENGTH_MERGED_KM, DIAMETER, DIAMETER_UNITS, FUEL_SOURCE,
                    START_LOCATION, START_PREFECTURE_DISTRICT, START_STATE_PROVINCE,
                    START_COUNTRY_OR_AREA, START_REGION, START_SUB_REGION, END_LOCATION,
                    END_PREFECTURE_DISTRICT, END_STATE_PROVINCE, END_COUNTRY_OR_AREA,
                    END_REGION, END_SUB_REGION, PROJECT_LEVEL_COST,
                    PROJECT_LEVEL_COST_UNITS, COST_USD, FID_STATUS, FID_YEAR,
                    PCI3, PCI4, PCI5, PCI6, ROUTE_ACCURACY, ROUTE_TYPE, GEOMETRY
                )
                SELECT
                f.value:properties:ProjectID::STRING,
                f.value:properties:PipelineName::STRING,
                f.value:properties:SegmentName::STRING,
                f.value:properties:Wiki::STRING,
                f.value:properties:Status::STRING,
                f.value:properties:LastUpdated::STRING,
                f.value:properties:Fuel::STRING,
                f.value:properties:OtherEnglishNames::STRING,
                f.value:properties:OtherLanguagePrimaryPipelineName::STRING,
                f.value:properties:OtherLanguageSegmentName::STRING,
                f.value:properties:CountriesOrAreas::STRING,
                f.value:properties:Owner::STRING,
                f.value:properties:ParentEntityIDs::STRING,
                f.value:properties:Parent::STRING,
                f.value:properties:StartYear1::STRING,
                f.value:properties:StartYear2::STRING,
                f.value:properties:StartYear3::STRING,
                f.value:properties:ShelvedYear::STRING,
                f.value:properties:CancelledYear::STRING,
                f.value:properties:StopYear::STRING,
                f.value:properties:Capacity::STRING,
                f.value:properties:CapacityUnits::STRING,
                f.value:properties:CapacityBcm_y::STRING,
                f.value:properties:CapacityBOEd::STRING,
                f.value:properties:LengthKnownKm::STRING,
                f.value:properties:LengthEstimateKm::STRING,
                f.value:properties:LengthMergedKm::STRING,
                f.value:properties:Diameter::STRING,
                f.value:properties:DiameterUnits::STRING,
                f.value:properties:FuelSource::STRING,
                f.value:properties:StartLocation::STRING,
                f.value:properties:"StartPrefecture/District"::STRING,
                f.value:properties:"StartState/Province"::STRING,
                f.value:properties:StartCountryOrArea::STRING,
                f.value:properties:StartRegion::STRING,
                f.value:properties:StartSubRegion::STRING,
                f.value:properties:EndLocation::STRING,
                f.value:properties:"EndPrefecture/District"::STRING,
                f.value:properties:"EndState/Province"::STRING,
                f.value:properties:EndCountryOrArea::STRING,
                f.value:properties:EndRegion::STRING,
                f.value:properties:EndSubRegion::STRING,
                f.value:properties:ProjectLevelCost::STRING,
                f.value:properties:ProjectLevelCostUnits::STRING,
                f.value:properties:CostUSD::STRING,
                f.value:properties:FIDStatus::STRING,
                f.value:properties:FIDYear::STRING,
                f.value:properties:PCI3::STRING,
                f.value:properties:PCI4::STRING,
                f.value:properties:PCI5::STRING,
                f.value:properties:PCI6::STRING,
                f.value:properties:RouteAccuracy::STRING,
                f.value:properties:RouteType::STRING,
                f.value:geometry
                FROM
                    @{database}.{schema}.GLOBAL_ENERGY_MONITOR_STAGE
                        (FILE_FORMAT => {database}.{schema}.GEOJSON_FF) x,
                    LATERAL FLATTEN(input => x.$1:features) f
                WHERE
                    METADATA$FILENAME ILIKE '%Pipeline%.geojson'
                """)

            cur.execute(f"SELECT COUNT(*) FROM {qname(database, schema, term_target_table)}")
            term_tgt_cnt = cur.fetchone()[0]
            cur.execute(f"SELECT COUNT(*) FROM {qname(database, schema, pipe_target_table)}")
            pipe_tgt_cnt = cur.fetchone()[0]
            logger.info("Counts: TERMINAL=%s , PIPELINE=%s", term_tgt_cnt, pipe_tgt_cnt)

        finally:
            try:
                cur.close()
            except Exception:
                pass
            conn.close()


if __name__ == "__main__":
    main()
