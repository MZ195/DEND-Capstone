import pandas as pd
import numpy as np
import json
import pyspark.sql.functions as f
from pyspark.sql import SparkSession, SQLContext, GroupedData
from pyspark.sql.functions import udf
from pyspark.sql.types import StringType
import datetime
import os


def create_spark_session():
    spark = SparkSession.builder.config(
        "spark.jars.packages", "saurfang:spark-sas7bdat:2.0.0-s_2.11").enableHiveSupport().getOrCreate()
    sqlContext = SQLContext(spark)
    sqlContext.setConf("spark.sql.autoBroadcastJoinThreashold", "0")
    return spark


def process_immigration_df(path, spark, us_code_state, city_code, immigration_code):
    """
    Process immigration data and create a table for that

    Keyword arguments:
    path              -- Current file path
    spark             -- Spark session object
    us_code_state     -- Tuples of Code: state pairs
    city_code.        -- Tuples of Code: city pairs
    immigration_code  -- Tuples of Code: race pairs
    """
    months = ['jan', 'feb', 'mar', 'apr', 'may', 'jun',
              'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
    fileName = '../../data/18-83510-I94-Data-2016/i94_{}16_sub.sas7bdat'

    code_state_udf = udf(lambda state: us_code_state[state], StringType())
    city_code_udf = udf(lambda code: city_code[code], StringType())
    country_code_udf = udf(lambda code: immigration_code[code], StringType())

    for month in months:
        current_month_filename = fileName.format(month)
        print(current_month_filename)
        immigration_df = spark.read.format(
            'com.github.saurfang.sas.spark').load(current_month_filename)

        immigrationMonthDF = immigration_df.filter(immigration_df.i94addr.isNotNull())\
            .filter(immigration_df.i94res.isNotNull())\
            .filter(f.col("i94addr").isin(list(us_code_state.keys())))\
            .filter(f.col("i94port").isin(list(city_code.keys())))\
            .withColumn("i94res", f.col("i94res").cast("integer").cast("string"))\
            .withColumn("origin_country", country_code_udf(f.col("i94res")))\
            .withColumn("State", code_state_udf(f.col("i94addr")))\
            .withColumn("id", f.col("cicid").cast("integer"))\
            .withColumn("state_code", f.col("i94addr"))\
            .withColumn("city_code", f.col("i94port"))\
            .withColumn("year", f.col("i94yr").cast("integer"))\
            .withColumn("month", f.col("i94mon").cast("integer"))\
            .withColumn("city", city_code_udf(f.col("i94port")))

        immigrationMonthDF.select('id', 'year', 'month', 'origin_country', 'city_code',
                                  'city', 'state_code', 'State').write.mode('append').parquet(path + '/immigration_data')


def process_demographics_df(path, us_code_state):
    """
    Process demographics data and create a table for that

    Keyword arguments:
    path              -- Current file path
    us_code_state     -- Tuples of Code: state pairs
    """
    demographics_df = pd.read_csv(
        path + "/us-cities-demographics.csv", sep=";")

    demographics_df.loc[demographics_df["Race"] ==
                        "American Indian and Alaska Native", "Race"] = "Native"
    demographics_df.loc[demographics_df["Race"] ==
                        "Black or African-American", "Race"] = "Afroamerican"
    demographics_df.loc[demographics_df["Race"] ==
                        "Hispanic or Latino", "Race"] = "Latino"

    us_demographics_avg_df = demographics_df.groupby(
        ['State', 'State Code', 'Race'])['Median Age'].mean()
    us_demographics_sum_df = demographics_df.groupby(['State', 'State Code', 'Race'])[
        'Total Population', 'Count', 'Male Population', 'Female Population', 'Number of Veterans', 'Foreign-born'].sum()

    us_demographics_df = pd.concat(
        [us_demographics_sum_df, us_demographics_avg_df], axis=1)
    us_demographics_df.reset_index(inplace=True)
    us_demographics_df["state_code"] = us_demographics_df["State Code"]
    us_demographics_df["median_age"] = us_demographics_df["Median Age"]

    for state_code in us_code_state:
        df = us_demographics_df.loc[us_demographics_df['state_code'] == state_code]
        total_population = df['Total Population'].max()
        male_population = df['Male Population'].max()
        female_population = df['Female Population'].max()
        veterans_population = df['Number of Veterans'].max()
        foreign_population = df['Foreign-born'].max()
        median_age = df['median_age'].max()

        us_demographics_df.loc[us_demographics_df['state_code']
                               == state_code, 'Total Population'] = total_population
        us_demographics_df.loc[us_demographics_df['state_code']
                               == state_code, 'Male Population'] = male_population
        us_demographics_df.loc[us_demographics_df['state_code']
                               == state_code, 'Female Population'] = female_population
        us_demographics_df.loc[us_demographics_df['state_code'] ==
                               state_code, 'Number of Veterans'] = veterans_population
        us_demographics_df.loc[us_demographics_df['state_code']
                               == state_code, 'Foreign-born'] = foreign_population
        us_demographics_df.loc[us_demographics_df['state_code']
                               == state_code, 'median_age'] = median_age

    us_demographics_df["percentage_male"] = us_demographics_df.apply(
        lambda row: float(row["Male Population"]/row["Total Population"])*100.0, axis=1)
    us_demographics_df["percentage_female"] = us_demographics_df.apply(
        lambda row: float(row["Female Population"]/row["Total Population"])*100.0, axis=1)
    us_demographics_df["percentage_veterans"] = us_demographics_df.apply(
        lambda row: float(row["Number of Veterans"]/row["Total Population"])*100.0, axis=1)
    us_demographics_df["percentage_foreign_born"] = us_demographics_df.apply(
        lambda row: float(row["Foreign-born"]/row["Total Population"])*100.0, axis=1)
    us_demographics_df["percentage_race"] = us_demographics_df.apply(
        lambda row: float(row["Count"]/row["Total Population"])*100.0, axis=1)

    us_df_demographics = pd.pivot_table(us_demographics_df, values='percentage_race', index=[
                                        "State", "state_code", "median_age", "percentage_male", "percentage_female", "percentage_veterans", "percentage_foreign_born"], columns=["Race"], aggfunc=np.mean, fill_value=0)
    us_df_demographics = pd.DataFrame(us_df_demographics.to_records())

    us_df_demographics.to_csv(
        path + "/us_demographics.csv", index=False)


def process_airport_df(path):
    """
    Process airport data and create a table for that

    Keyword arguments:
    path              -- Current file path
    """
    airport_df = pd.read_csv(path + "/airport-codes_csv.csv")

    us_airport_df = airport_df[airport_df["iso_country"] == "US"]
    us_airport_df = us_airport_df[(us_airport_df["type"] == "small_airport") | (
        us_airport_df["type"] == "medium_airport") | (us_airport_df["type"] == "large_airport")]

    us_airport_df["elevation_ft"] = us_airport_df.apply(
        lambda row: float(row["elevation_ft"]), axis=1)
    us_airport_df["state_code"] = us_airport_df.apply(
        lambda row: row["iso_region"].split("-")[-1], axis=1)
    us_airport_df["x_coordinate"] = us_airport_df.apply(
        lambda row: float(row["coordinates"].split(",")[0]), axis=1)
    us_airport_df["y_coordinate"] = us_airport_df.apply(
        lambda row: float(row["coordinates"].split(",")[-1]), axis=1)

    us_airport_df["country"] = us_airport_df["iso_country"]
    us_airport_df["city_code"] = us_airport_df["local_code"]
    us_airport_df = us_airport_df[["ident", "type", "name", "elevation_ft", "country",
                                   "state_code", "city_code", "municipality", "x_coordinate", "y_coordinate"]]

    us_airport_df.to_csv(path + "/us_airports.csv", index=False)


def run():
    dir_path = os.path.dirname(os.path.realpath(__file__))
    utils_data = json.load(open(dir_path + '/utils.json'))
    us_state_code = utils_data['us_state_code']
    us_code_state = {state: code for code, state in us_state_code.items()}
    city_code = utils_data['city_codes']
    immigration_code = utils_data['immigration_codes']

    spark = create_spark_session()
    process_airport_df(dir_path)
    process_demographics_df(dir_path, us_code_state)
    process_immigration_df(dir_path, spark, us_code_state,
                           city_code, immigration_code)


if __name__ == "__main__":
    run()
