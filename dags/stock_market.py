from airflow.decorators import dag, task
from airflow.hooks.base import BaseHook
from airflow.sensors.base import PokeReturnValue
from airflow.operators.python import PythonOperator
from airflow.providers.docker.operators.docker import DockerOperator
from datetime import datetime
import requests
from include.stock_market.tasks import (
    _get_stock_prices,
    _store_prices,
    _get_formatted_prices_from_minio,
)
from astro import sql as aql
from astro.files import File
from astro.sql.table import Table, Metadata
import sqlalchemy


@dag(
    start_date=datetime(2025, 1, 1),
    schedule="@daily",
    catchup=False,
    tags=["stock_market"],
)
def stock_market():

    @task.sensor(poke_interval=30, timeout=300, mode="poke")
    def is_api_available():
        api = BaseHook.get_connection("stock_api")
        url = f"{api.host}{api.extra_dejson['endpoint']}"
        response = requests.get(url, headers=api.extra_dejson["headers"])
        condition = response.json()["finance"]["result"] is None

        return PokeReturnValue(is_done=condition, xcom_value=url)

    get_stock_prices = PythonOperator(
        task_id="get_stock_prices",
        python_callable=_get_stock_prices,
        op_kwargs={
            "url": '{{ task_instance.xcom_pull(task_ids="is_api_available") }}',
            "symbol": "AAPL",
        },
    )

    store_prices = PythonOperator(
        task_id="store_prices",
        python_callable=_store_prices,
        op_kwargs={
            "prices": '{{ task_instance.xcom_pull(task_ids="get_stock_prices") }}'
        },
    )

    format_prices = DockerOperator(
        task_id="format_prices",
        max_active_tis_per_dag=1,
        image="stock/spark-app",
        container_name="format_prices",
        environment={
            "SPARK_APPLICATION_ARGS": '{{ task_instance.xcom_pull(task_ids="store_prices")}}'
        },
        api_version="auto",
        auto_remove=True,
        docker_url="tcp://docker-proxy:2375",
        network_mode="container:spark-master",
        tty=True,
        xcom_all=False,
        mount_tmp_dir=False,
    )

    get_formatted_csv = PythonOperator(
        task_id="get_formatted_csv",
        python_callable=_get_formatted_prices_from_minio,
        op_kwargs={
            "location": '{{ task_instance.xcom_pull(task_ids="store_prices") }}'
        },
    )

    load_to_dw = aql.load_file(
        task_id="load_to_dw",
        input_file=File(
            path='{{ task_instance.xcom_pull(task_ids="get_formatted_csv") }}',
            conn_id="minio",
        ),
        output_table=Table(
            name="stock_prices",
            conn_id="postgres",
            metadata=Metadata(schema="public"),
            columns=[
                sqlalchemy.Column("timestamp", sqlalchemy.BigInteger, primary_key=True),
                sqlalchemy.Column("close", sqlalchemy.Float),
                sqlalchemy.Column("high", sqlalchemy.Float),
                sqlalchemy.Column("low", sqlalchemy.Float),
                sqlalchemy.Column("open", sqlalchemy.Float),
                sqlalchemy.Column("volume", sqlalchemy.Integer),
                sqlalchemy.Column("date", sqlalchemy.Date),
            ],
        ),
    )

    (
        is_api_available()
        >> get_stock_prices
        >> store_prices
        >> format_prices
        >> get_formatted_csv
        >> load_to_dw
    )


stock_market()
