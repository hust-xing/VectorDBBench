import logging
from contextlib import contextmanager
import time
from typing import Iterable, Type
from ..api import VectorDB, DBCaseConfig, DBConfig, IndexType
from .config import AWSOpenSearchConfig, AWSOpenSearchIndexConfig
from opensearchpy import OpenSearch
from opensearchpy.helpers import bulk

log = logging.getLogger(__name__)


class AWSOpenSearch(VectorDB):
    def __init__(
        self,
        dim: int,
        db_config: dict,
        db_case_config: AWSOpenSearchIndexConfig,
        index_name: str = "vdb_bench_index",  # must be lowercase
        id_col_name: str = "id",
        vector_col_name: str = "embedding",
        drop_old: bool = False,
        **kwargs,
    ):
        self.dim = dim
        self.db_config = db_config
        self.case_config = db_case_config
        self.index_name = index_name
        self.id_col_name = id_col_name
        self.category_col_names = [
            f"scalar-{categoryCount}" for categoryCount in [2, 5, 10, 100, 1000]
        ]
        self.vector_col_name = vector_col_name

        log.info(f"AWS_OpenSearch client config: {self.db_config}")
        client = OpenSearch(**self.db_config)
        if drop_old:
            log.info(f"AWS_OpenSearch client drop old index: {self.index_name}")
            is_existed = client.indices.exists(index=self.index_name)
            if is_existed:
                client.indices.delete(index=self.index_name)
            self._create_index(client)

    @classmethod
    def config_cls(cls) -> AWSOpenSearchConfig:
        return AWSOpenSearchConfig

    @classmethod
    def case_config_cls(
        cls, index_type: IndexType | None = None
    ) -> AWSOpenSearchIndexConfig:
        return AWSOpenSearchIndexConfig

    def _create_index(self, client: OpenSearch):
        settings = {
            "index": {
                "knn": True,
                # "number_of_shards": 5,
                # "refresh_interval": "600s",
            }
        }
        mappings = {
            "properties": {
                self.id_col_name: {"type": "integer"},
                **{
                    categoryCol: {"type": "keyword"}
                    for categoryCol in self.category_col_names
                },
                self.vector_col_name: {
                    "type": "knn_vector",
                    "dimension": self.dim,
                    "method": self.case_config.index_param(),
                },
            }
        }
        try:
            client.indices.create(
                index=self.index_name, body=dict(settings=settings, mappings=mappings)
            )
        except Exception as e:
            log.warning(f"Failed to create index: {self.index_name} error: {str(e)}")
            raise e from None

    @contextmanager
    def init(self) -> None:
        """connect to elasticsearch"""
        self.client = OpenSearch(**self.db_config)

        yield
        # self.client.transport.close()
        self.client = None
        del self.client

    def insert_embeddings(
        self,
        embeddings: Iterable[list[float]],
        metadata: list[int],
        **kwargs,
    ) -> tuple[int, Exception]:
        """Insert the embeddings to the elasticsearch."""
        assert self.client is not None, "should self.init() first"

        insert_data = []
        for i in range(len(embeddings)):
            insert_data.append({"index": {"_index": self.index_name, "_id": metadata[i]}})
            insert_data.append({self.vector_col_name: embeddings[i]})
        try:
            resp = self.client.bulk(insert_data)
            log.info(f"AWS_OpenSearch adding documents: {len(resp['items'])}")
            resp = self.client.indices.stats(self.index_name)
            log.info(f"Total document count in index: {resp['_all']['primaries']['indexing']['index_total']}")
            return (len(embeddings), None)
        except Exception as e:
            log.warning(f"Failed to insert data: {self.index_name} error: {str(e)}")
            time.sleep(10)
            return self.insert_embeddings(embeddings, metadata)

    def search_embedding(
        self,
        query: list[float],
        k: int = 100,
        filters: dict | None = None,
    ) -> list[int]:
        """Get k most similar embeddings to query vector.

        Args:
            query(list[float]): query embedding to look up documents similar to.
            k(int): Number of most similar embeddings to return. Defaults to 100.
            filters(dict, optional): filtering expression to filter the data while searching.

        Returns:
            list[tuple[int, float]]: list of k most similar embeddings in (id, score) tuple to the query embedding.
        """
        assert self.client is not None, "should self.init() first"

        body = {
            "size": k,
            "query": {"knn": {self.vector_col_name: {"vector": query, "k": k}}},
        }
        try:
            resp = self.client.search(index=self.index_name, body=body)
            log.info(f'Search took: {resp["took"]}')
            log.info(f'Search shards: {resp["_shards"]}')
            log.info(f'Search hits total: {resp["hits"]["total"]}')
            result = [int(d["_id"]) for d in resp["hits"]["hits"]]
            # log.info(f'success! length={len(res)}')

            return result
        except Exception as e:
            log.warning(f"Failed to search: {self.index_name} error: {str(e)}")
            raise e from None

    def optimize(self):
        """optimize will be called between insertion and search in performance cases."""
        pass

    def ready_to_load(self):
        """ready_to_load will be called before load in load cases."""
        pass
