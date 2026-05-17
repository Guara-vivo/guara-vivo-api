import json
import logging
import os

import pika
from dotenv import load_dotenv


load_dotenv()


logger = logging.getLogger(__name__)

RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
RABBITMQ_PORT = int(os.getenv("RABBITMQ_PORT", "5672"))
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "guest")
RABBITMQ_PASSWORD = os.getenv("RABBITMQ_PASSWORD", "guest")
QUEUE_NAME = os.getenv("QUEUE_NAME", "guara-vermelho-inference")


def get_connection() -> pika.BlockingConnection:
    credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASSWORD)
    parameters = pika.ConnectionParameters(
        host=RABBITMQ_HOST,
        port=RABBITMQ_PORT,
        credentials=credentials,
        heartbeat=60,
        blocked_connection_timeout=10,
        connection_attempts=3,
        retry_delay=1,
        socket_timeout=5,
    )
    return pika.BlockingConnection(parameters)


def publish_record_for_inference(record_id: int) -> None:
    payload = json.dumps({"record_id": record_id}).encode("utf-8")
    connection = get_connection()
    try:
        channel = connection.channel()
        channel.queue_declare(queue=QUEUE_NAME, durable=True)
        channel.basic_publish(
            exchange="",
            routing_key=QUEUE_NAME,
            body=payload,
            properties=pika.BasicProperties(
                delivery_mode=pika.DeliveryMode.Persistent,
                content_type="application/json",
            ),
        )
        logger.info("queued record %s for inference", record_id)
    finally:
        connection.close()
