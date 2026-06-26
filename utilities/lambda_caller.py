import boto3
import json
import os


def invoke_async(function_name: str, payload: dict):
    try:
        client = boto3.client("lambda", region_name=os.environ.get("AWS_REGION_NAME", "ap-south-1"))
        client.invoke(
            FunctionName=function_name,
            InvocationType="Event",
            Payload=json.dumps(payload),
        )
    except Exception as e:
        print(f"[LambdaCaller] Async invoke {function_name} failed (non-fatal): {e}")


def invoke_sync(function_name: str, payload: dict) -> dict:
    client = boto3.client("lambda", region_name=os.environ.get("AWS_REGION_NAME", "ap-south-1"))
    resp = client.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload),
    )
    return json.loads(resp["Payload"].read())
