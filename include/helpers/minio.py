from minio import Minio
from airflow.hooks.base import BaseHook

def get_minio_client():
    minio = BaseHook.get_connection('minio')
    print('degug minio')
    print(minio)
    client = Minio(
        endpoint=minio.extra_dejson['endpoint_url'].split('//')[1],
        access_key=minio.login,
        secret_key=minio.password,
        secure=False
    )
    return client