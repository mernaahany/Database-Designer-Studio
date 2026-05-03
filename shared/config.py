"""
Project-wide configuration.
"""
from __future__ import annotations

from typing import Any

from dotenv import load_dotenv
from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
import os

load_dotenv()

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # Azure OpenAI
    azure_openai_endpoint: str = Field(
        default="",
        validation_alias=AliasChoices("AZURE_OPENAI_ENDPOINT"),
    )
    azure_openai_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("AZURE_OPENAI_API_KEY"),
    )
    azure_openai_api_version: str = Field(
        default="2024-05-01-preview",
        validation_alias=AliasChoices("AZURE_OPENAI_API_VERSION"),
    )
    azure_chat_deployment: str = Field(
        default="gpt-4.1-mini",
        validation_alias=AliasChoices(
            "AZURE_OPENAI_DEPLOYMENT",
        ),
    )
    azure_embedding_deployment: str = Field(
        default="text-embedding-3-small",
        validation_alias=AliasChoices(
            "AZURE_OPENAI_EMBEDDING_DEPLOYMENT",
        ),
    )

    # Azure Blob Storage
    azure_storage_connection_string: str = Field(
        default="",
        validation_alias=AliasChoices(
            "AZURE_STORAGE_CONNECTION_STRING",
            
        ),
    )
    azure_storage_account_name: str = Field(
        default="",
        validation_alias=AliasChoices("AZURE_STORAGE_ACCOUNT_NAME", "ACCOUNT_NAME"),
    )
    azure_storage_account_key: str = Field(
        default="",
        validation_alias=AliasChoices("AZURE_STORAGE_ACCOUNT_KEY", "ACCOUNT_KEY"),
    )
    azure_storage_account_url: str = Field(
        default="",
        validation_alias=AliasChoices("AZURE_STORAGE_ACCOUNT_URL", "ACCOUNT_URL"),
    )
    azure_blob_sas_token: str = Field(
        default="",
        validation_alias=AliasChoices("AZURE_BLOB_SAS_TOKEN", "SAS_TOKEN"),
    )
    azure_blob_sas_url: str = Field(
        default="",
        validation_alias=AliasChoices("AZURE_BLOB_SAS_URL"),
    )
    blob_container_active: str = Field(
        default="db-active",
        validation_alias=AliasChoices("BLOB_CONTAINER_ACTIVE", "CONTAINER"),
    )
    blob_container_backups: str = Field(
        default="",
        validation_alias=AliasChoices("BLOB_CONTAINER_BACKUPS"),
    )
    workspace_container: str = Field(
        default="workspaces",
        validation_alias=AliasChoices("WORKSPACE_CONTAINER"),
    )

    # Database / memory
    db_url: str = Field(default="", validation_alias=AliasChoices("DB_URL"))
    database_schema: str = Field(default="public", validation_alias=AliasChoices("DATABASE_SCHEMA"))
    memory_dir: str = Field(default="/tmp/db_designer_sessions", validation_alias=AliasChoices("MEMORY_DIR"))

    # Feature tuning
    max_validation_iterations: int = Field(default=5, validation_alias=AliasChoices("MAX_VALIDATION_ITERATIONS"))
    max_correction_attempts: int = Field(default=3, validation_alias=AliasChoices("MAX_CORRECTION_ATTEMPTS"))
    confidence_threshold: float = Field(default=0.5, validation_alias=AliasChoices("CONFIDENCE_THRESHOLD"))
    retriever_top_k: int = Field(default=3, validation_alias=AliasChoices("RETRIEVER_TOP_K"))
    retriever_dense_top_k: int = Field(default=10, validation_alias=AliasChoices("RETRIEVER_DENSE_TOP_K"))
    retriever_bm25_top_k: int = Field(default=10, validation_alias=AliasChoices("RETRIEVER_BM25_TOP_K"))
    retriever_rrf_k: int = Field(default=60, validation_alias=AliasChoices("RETRIEVER_RRF_K"))
    retriever_dense_weight: float = Field(default=0.5, validation_alias=AliasChoices("RETRIEVER_DENSE_WEIGHT"))
    retriever_bm25_weight: float = Field(default=0.5, validation_alias=AliasChoices("RETRIEVER_BM25_WEIGHT"))
    retriever_complexity_filter: bool = Field(default=False, validation_alias=AliasChoices("RETRIEVER_COMPLEXITY_FILTER"))
    retriever_max_tables_filter: int = Field(default=10, validation_alias=AliasChoices("RETRIEVER_MAX_TABLES_FILTER"))
    no_of_samples: int = Field(default=7, validation_alias=AliasChoices("NO_OF_SAMPLES"))
    require_human_approval: bool = Field(default=False, validation_alias=AliasChoices("REQUIRE_HUMAN_APPROVAL"))

    @property
    def resolved_blob_container_backups(self) -> str:
        return self.blob_container_backups or f"{self.blob_container_active}-backups"

    def missing_openai_settings(self) -> list[str]:
        missing = []
        if not self.azure_openai_api_key:
            missing.append("AZURE_OPENAI_API_KEY")
        if not self.azure_openai_endpoint:
            missing.append("AZURE_OPENAI_ENDPOINT")
        return missing

    def missing_blob_settings(self) -> list[str]:
        has_connection_string = bool(self.azure_storage_connection_string)
        has_account_pair = bool(self.azure_storage_account_name and self.azure_storage_account_key)
        has_sas = bool(self.azure_storage_account_url and self.azure_blob_sas_token)
        if has_connection_string or has_account_pair or has_sas:
            return []
        return [
            "AZURE_STORAGE_CONNECTION_STRING / AZURE_CONN_STR / CONNECTION_STRING",
            "or AZURE_STORAGE_ACCOUNT_NAME + AZURE_STORAGE_ACCOUNT_KEY",
            "or AZURE_STORAGE_ACCOUNT_URL + AZURE_BLOB_SAS_TOKEN",
        ]

    def validate_required_settings(self) -> dict[str, list[str]]:
        return {
            "openai": self.missing_openai_settings(),
            "blob_storage": self.missing_blob_settings(),
        }


settings = Settings()


# Compatibility constants for existing imports.
AZURE_OPENAI_API_KEY = settings.azure_openai_api_key
AZURE_OPENAI_ENDPOINT = settings.azure_openai_endpoint
AZURE_OPENAI_API_VERSION = settings.azure_openai_api_version
AZURE_OPENAI_DEPLOYMENT = settings.azure_chat_deployment
AZURE_OPENAI_EMBEDDING_DEPLOYMENT = settings.azure_embedding_deployment

AZURE_STORAGE_CONNECTION_STRING = settings.azure_storage_connection_string
AZURE_STORAGE_ACCOUNT_NAME = settings.azure_storage_account_name
AZURE_STORAGE_ACCOUNT_KEY = settings.azure_storage_account_key
AZURE_STORAGE_ACCOUNT_URL = settings.azure_storage_account_url
AZURE_BLOB_SAS_TOKEN = settings.azure_blob_sas_token
AZURE_BLOB_SAS_URL = settings.azure_blob_sas_url

BLOB_CONTAINER_ACTIVE = settings.blob_container_active
BLOB_CONTAINER_BACKUPS = settings.resolved_blob_container_backups
WORKSPACE_CONTAINER = settings.workspace_container

DB_URL = settings.db_url
DATABASE_SCHEMA = settings.database_schema
MEMORY_DIR = settings.memory_dir

MAX_VALIDATION_ITERATIONS = settings.max_validation_iterations
MAX_CORRECTION_ATTEMPTS = settings.max_correction_attempts
CONFIDENCE_THRESHOLD = settings.confidence_threshold
RETRIEVER_TOP_K = settings.retriever_top_k
RETRIEVER_DENSE_TOP_K = settings.retriever_dense_top_k
RETRIEVER_BM25_TOP_K = settings.retriever_bm25_top_k
RETRIEVER_RRF_K = settings.retriever_rrf_k
RETRIEVER_DENSE_WEIGHT = settings.retriever_dense_weight
RETRIEVER_BM25_WEIGHT = settings.retriever_bm25_weight
RETRIEVER_COMPLEXITY_FILTER = settings.retriever_complexity_filter
RETRIEVER_MAX_TABLES_FILTER = settings.retriever_max_tables_filter
NO_OF_SAMPLES = settings.no_of_samples
REQUIRE_HUMAN_APPROVAL = settings.require_human_approval


def get_chat_llm(temperature: float = 0.0) -> AzureChatOpenAI:
    return AzureChatOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
        azure_deployment=settings.azure_chat_deployment,
        temperature=temperature,
    )


def get_llm(temperature: float = 0.0) -> AzureChatOpenAI:
    return get_chat_llm(temperature=temperature)


def get_embeddings() -> AzureOpenAIEmbeddings:
    return AzureOpenAIEmbeddings(
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
        azure_deployment=settings.azure_embedding_deployment,
    )


def missing_openai_settings() -> list[str]:
    return settings.missing_openai_settings()


def missing_blob_settings() -> list[str]:
    return settings.missing_blob_settings()


def validate_required_settings() -> dict[str, list[str]]:
    return settings.validate_required_settings()


__all__ = [
    "AZURE_BLOB_SAS_TOKEN",
    "AZURE_BLOB_SAS_URL",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_API_VERSION",
    "AZURE_OPENAI_DEPLOYMENT",
    "AZURE_OPENAI_EMBEDDING_DEPLOYMENT",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_STORAGE_ACCOUNT_KEY",
    "AZURE_STORAGE_ACCOUNT_NAME",
    "AZURE_STORAGE_ACCOUNT_URL",
    "AZURE_STORAGE_CONNECTION_STRING",
    "BLOB_CONTAINER_ACTIVE",
    "BLOB_CONTAINER_BACKUPS",
    "CONFIDENCE_THRESHOLD",
    "DATABASE_SCHEMA",
    "DB_URL",
    "MAX_CORRECTION_ATTEMPTS",
    "MAX_VALIDATION_ITERATIONS",
    "MEMORY_DIR",
    "NO_OF_SAMPLES",
    "REQUIRE_HUMAN_APPROVAL",
    "RETRIEVER_BM25_TOP_K",
    "RETRIEVER_COMPLEXITY_FILTER",
    "RETRIEVER_DENSE_TOP_K",
    "RETRIEVER_DENSE_WEIGHT",
    "RETRIEVER_MAX_TABLES_FILTER",
    "RETRIEVER_RRF_K",
    "RETRIEVER_TOP_K",
    "RETRIEVER_BM25_WEIGHT",
    "WORKSPACE_CONTAINER",
    "Settings",
    "get_chat_llm",
    "get_embeddings",
    "get_llm",
    "missing_blob_settings",
    "missing_openai_settings",
    "settings",
    "validate_required_settings",
]
