"""Milvus 集合与向量索引；首次使用时创建，维度改变时检查已有数据。"""

from pymilvus import DataType, MilvusClient

from app.core.config import COLLECTION_NAME, MILVUS_URI

_client: MilvusClient | None = None


def get_vector_client(dimension: int) -> MilvusClient:
    global _client
    if _client is None:
        _client = MilvusClient(uri=MILVUS_URI)
        if _client.has_collection(collection_name=COLLECTION_NAME):
            description = _client.describe_collection(collection_name=COLLECTION_NAME)
            vector_field = next(
                (
                    field
                    for field in description.get("fields", [])
                    if field["name"] == "vector"
                ),
                None,
            )
            existing_dimension = (
                int(vector_field.get("params", {}).get("dim", 0)) if vector_field else 0
            )
            if existing_dimension != dimension:
                stats = _client.get_collection_stats(collection_name=COLLECTION_NAME)
                if int(stats.get("row_count", 0)):
                    raise ValueError(
                        "Embedding 模型的向量维度与现有资料库不一致，请清空旧向量库后重新导入资料"
                    )
                _client.drop_collection(collection_name=COLLECTION_NAME)
        if not _client.has_collection(collection_name=COLLECTION_NAME):
            schema = _client.create_schema(auto_id=True, enable_dynamic_field=False)
            schema.add_field(field_name="id", datatype=DataType.INT64, is_primary=True)
            schema.add_field(
                field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=dimension
            )
            schema.add_field(
                field_name="material_id", datatype=DataType.VARCHAR, max_length=64
            )
            schema.add_field(
                field_name="text", datatype=DataType.VARCHAR, max_length=12000
            )
            schema.add_field(
                field_name="source", datatype=DataType.VARCHAR, max_length=2048
            )
            schema.add_field(
                field_name="title", datatype=DataType.VARCHAR, max_length=512
            )
            index_params = _client.prepare_index_params()
            index_params.add_index(
                field_name="vector", index_type="AUTOINDEX", metric_type="COSINE"
            )
            _client.create_collection(
                collection_name=COLLECTION_NAME,
                schema=schema,
                index_params=index_params,
            )
        _client.load_collection(collection_name=COLLECTION_NAME)
    return _client
