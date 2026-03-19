"""
RAG (Retrieval Augmented Generation) Pipeline using LangChain + Ollama + Chroma.

This example demonstrates a RAG implementation using LangChain's ecosystem:
- LangChain Community DirectoryLoader + TextLoader for document ingestion
- RecursiveCharacterTextSplitter for chunking
- OllamaEmbeddings (nomic-embed-text) for vector embeddings
- Chroma for persistent vector storage and similarity search
- ChatOllama + LCEL chain (ChatPromptTemplate | ChatOllama | StrOutputParser) for generation

Compared to examples/ai/rag_agent_ollama.py (pure Flux + Ollama SDK + FAISS),
this variant uses LangChain's document loaders, text splitters, and Chroma vector store,
showing how LangChain's ecosystem integrates with Flux workflow orchestration.

RAG enhances LLM responses by retrieving relevant context from a document collection,
making it ideal for:
- Question answering over documentation
- Knowledge base chat
- Context-aware assistants
- Domain-specific applications

Prerequisites:
    1. Install Ollama: https://ollama.ai
    2. Pull required models:
       ollama pull llama3
       ollama pull nomic-embed-text
    3. Start Ollama service: ollama serve
    4. Install dependencies:
       pip install langchain-core langchain-ollama langchain-chroma langchain-community langchain-text-splitters

Usage:
    # Index documents from a directory
    flux workflow run rag_index_langchain '{
        "docs_path": "./examples/ai/docs",
        "collection_name": "flux_docs"
    }'

    # Query the indexed knowledge base
    flux workflow run rag_query_langchain '{
        "collection_name": "flux_docs",
        "query": "What are Flux workflows?"
    }'

    # Use different models
    flux workflow run rag_query_langchain '{
        "collection_name": "flux_docs",
        "query": "How does task caching work?",
        "llm_model": "qwen2.5:0.5b",
        "embedding_model": "nomic-embed-text"
    }'
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_chroma import Chroma
from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from flux import ExecutionContext, task, workflow


@task
async def load_documents(docs_path: str) -> list[Document]:
    """
    Load markdown documents from a directory using LangChain DirectoryLoader.

    Args:
        docs_path: Path to directory containing markdown files

    Returns:
        List of LangChain Document objects with content and metadata
    """
    docs_dir = Path(docs_path)

    if not docs_dir.exists():
        raise ValueError(f"Directory not found: {docs_path}")

    if not docs_dir.is_dir():
        raise ValueError(f"Path is not a directory: {docs_path}")

    loader = DirectoryLoader(
        str(docs_dir),
        glob="**/*.md",
        loader_cls=TextLoader,
        loader_kwargs={"encoding": "utf-8"},
        show_progress=False,
    )
    documents = loader.load()

    if not documents:
        raise ValueError(f"No markdown files found in: {docs_path}")

    return documents


@task
async def split_documents(
    documents: list[Document],
    chunk_size: int = 500,
    chunk_overlap: int = 50,
) -> list[Document]:
    """
    Split documents into smaller chunks using RecursiveCharacterTextSplitter.

    Args:
        documents: List of LangChain Document objects
        chunk_size: Maximum characters per chunk
        chunk_overlap: Character overlap between chunks

    Returns:
        List of chunked Document objects with updated metadata
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    chunks = splitter.split_documents(documents)
    return chunks


@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=120)
async def validate_embeddings(
    model: str = "nomic-embed-text",
    ollama_url: str = "http://localhost:11434",
) -> None:
    """
    Validate that the embedding model is available by running a test embed.

    Args:
        model: Ollama embedding model to use
        ollama_url: Ollama server URL
    """
    try:
        embeddings = OllamaEmbeddings(model=model, base_url=ollama_url)
        await embeddings.aembed_query("connectivity check")
    except Exception as e:
        raise RuntimeError(
            f"Failed to initialize embeddings: {str(e)}. "
            f"Make sure Ollama is running and model '{model}' is available. "
            f"Run: ollama pull {model}",
        ) from e


@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=300)
async def build_vector_store(
    chunks: list[Document],
    collection_name: str,
    embedding_model: str = "nomic-embed-text",
    ollama_url: str = "http://localhost:11434",
) -> str:
    """
    Build and persist a Chroma vector store from document chunks.

    Args:
        chunks: List of chunked Document objects
        collection_name: Name for the Chroma collection
        embedding_model: Ollama embedding model to use
        ollama_url: Ollama server URL

    Returns:
        Path to the persisted Chroma vector store directory
    """
    safe_name = collection_name.replace("/", "_").replace("\\", "_").replace("..", "_")
    persist_dir = Path.home() / ".flux" / "rag_indexes" / safe_name
    persist_dir.mkdir(parents=True, exist_ok=True)

    try:
        embeddings = OllamaEmbeddings(model=embedding_model, base_url=ollama_url)
        Chroma.from_documents(
            documents=chunks,
            embedding=embeddings,
            collection_name=collection_name,
            persist_directory=str(persist_dir),
        )
    except Exception as e:
        raise RuntimeError(
            f"Failed to build vector store: {str(e)}. "
            f"Check that the persist directory is writable: {persist_dir}",
        ) from e

    return str(persist_dir)


@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=120)
async def retrieve_relevant_chunks(
    query: str,
    collection_name: str,
    embedding_model: str = "nomic-embed-text",
    ollama_url: str = "http://localhost:11434",
    top_k: int = 3,
) -> list[Document]:
    """
    Load persisted Chroma vector store and retrieve relevant document chunks.

    Args:
        query: User's question
        collection_name: Name of the Chroma collection to load
        embedding_model: Ollama embedding model used at index time
        ollama_url: Ollama server URL
        top_k: Number of chunks to retrieve

    Returns:
        List of most relevant LangChain Document objects
    """
    safe_name = collection_name.replace("/", "_").replace("\\", "_").replace("..", "_")
    persist_dir = Path.home() / ".flux" / "rag_indexes" / safe_name

    if not persist_dir.exists():
        raise ValueError(
            f"Vector store '{collection_name}' not found at {persist_dir}. "
            "Run rag_index_langchain first.",
        )

    try:
        embeddings = OllamaEmbeddings(model=embedding_model, base_url=ollama_url)
        vector_store = Chroma(
            collection_name=collection_name,
            embedding_function=embeddings,
            persist_directory=str(persist_dir),
        )
        docs = await vector_store.asimilarity_search(query, k=top_k)
        return docs

    except Exception as e:
        raise RuntimeError(
            f"Failed to retrieve chunks: {str(e)}. "
            f"Make sure the index '{collection_name}' exists and Ollama is running "
            f"with model '{embedding_model}' available.",
        ) from e


@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=60)
async def generate_rag_response(
    query: str,
    retrieved_docs: list[Document],
    model: str = "llama3",
    ollama_url: str = "http://localhost:11434",
) -> dict[str, Any]:
    """
    Generate a response using an LCEL chain with the retrieved document context.

    The retrieval step is already complete — docs are passed directly rather than
    wiring a retriever into the chain.

    Args:
        query: User's question
        retrieved_docs: Documents already retrieved in the retrieve_relevant_chunks stage
        model: Ollama LLM model to use
        ollama_url: Ollama server URL

    Returns:
        Dictionary with answer and source metadata
    """
    try:
        context = "\n\n".join(
            f"[Source: {doc.metadata.get('source', 'unknown')}]\n{doc.page_content.strip()}"
            for doc in retrieved_docs
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful assistant that answers questions based on provided "
                    "documentation context. Always cite your sources.",
                ),
                (
                    "human",
                    "Context from documentation:\n\n{context}\n\nQuestion: {question}\n\n"
                    "Answer the question based on the context provided above. "
                    "If the context doesn't contain relevant information, say so clearly. "
                    "Be concise and accurate.",
                ),
            ],
        )

        llm = ChatOllama(model=model, base_url=ollama_url)
        chain = prompt | llm | StrOutputParser()

        answer = await chain.ainvoke({"context": context, "question": query})

        sources = [{"source": doc.metadata.get("source", "unknown")} for doc in retrieved_docs]

        return {
            "answer": answer,
            "sources": sources,
        }

    except Exception as e:
        raise RuntimeError(
            f"Failed to generate response: {str(e)}. "
            f"Make sure Ollama is running and model '{model}' is available. "
            f"Run: ollama pull {model}",
        ) from e


@workflow
async def rag_index_langchain(ctx: ExecutionContext[dict[str, Any]]):
    """
    Index documents into a Chroma vector store for later querying.

    This workflow loads markdown documents, splits them into chunks, generates
    embeddings with Ollama, and persists a Chroma vector store. Run this once,
    then use rag_query_langchain for queries.

    Input format:
    {
        "docs_path": "./path/to/docs",           # Required: path to markdown docs
        "collection_name": "my_docs",            # Required: unique collection name
        "chunk_size": 500,                       # Optional: chars per chunk
        "chunk_overlap": 50,                     # Optional: chunk overlap
        "embedding_model": "nomic-embed-text",   # Optional: Ollama embedding model
        "ollama_url": "http://localhost:11434"   # Optional: Ollama server URL
    }

    Returns:
        Dictionary with indexing results and vector store location
    """
    input_data = ctx.input or {}

    docs_path = input_data.get("docs_path")
    collection_name = input_data.get("collection_name")

    if not docs_path:
        return {
            "error": "Missing required parameter 'docs_path'",
            "execution_id": ctx.execution_id,
        }

    if not collection_name:
        return {
            "error": "Missing required parameter 'collection_name'",
            "execution_id": ctx.execution_id,
        }

    chunk_size = input_data.get("chunk_size", 500)
    chunk_overlap = input_data.get("chunk_overlap", 50)
    embedding_model = input_data.get("embedding_model", "nomic-embed-text")
    ollama_url = input_data.get("ollama_url", "http://localhost:11434")

    documents = await load_documents(docs_path)
    chunks = await split_documents(documents, chunk_size, chunk_overlap)

    if not chunks:
        return {
            "error": "No chunks created from documents",
            "execution_id": ctx.execution_id,
        }

    await validate_embeddings(embedding_model, ollama_url)
    persist_path = await build_vector_store(chunks, collection_name, embedding_model, ollama_url)

    return {
        "status": "indexed",
        "collection_name": collection_name,
        "persist_path": persist_path,
        "num_documents": len(documents),
        "num_chunks": len(chunks),
        "embedding_model": embedding_model,
        "execution_id": ctx.execution_id,
    }


@workflow
async def rag_query_langchain(ctx: ExecutionContext[dict[str, Any]]):
    """
    Query a pre-indexed Chroma vector store using RAG.

    This workflow loads a previously built Chroma collection and answers questions
    using retrieved context. Run rag_index_langchain first to create the index.

    Input format:
    {
        "collection_name": "my_docs",            # Required: name of pre-built collection
        "query": "Your question here",           # Required: question to answer
        "llm_model": "llama3",                   # Optional: Ollama LLM model
        "embedding_model": "nomic-embed-text",   # Optional: Ollama embedding model
        "top_k": 3,                              # Optional: chunks to retrieve
        "ollama_url": "http://localhost:11434"   # Optional: Ollama server URL
    }

    Returns:
        Dictionary with answer, sources, and query metadata
    """
    input_data = ctx.input or {}

    collection_name = input_data.get("collection_name")
    query = input_data.get("query")

    if not collection_name:
        return {
            "error": "Missing required parameter 'collection_name'",
            "execution_id": ctx.execution_id,
        }

    if not query:
        return {
            "error": "Missing required parameter 'query'",
            "execution_id": ctx.execution_id,
        }

    llm_model = input_data.get("llm_model", "llama3")
    embedding_model = input_data.get("embedding_model", "nomic-embed-text")
    top_k = input_data.get("top_k", 3)
    ollama_url = input_data.get("ollama_url", "http://localhost:11434")

    retrieved_docs = await retrieve_relevant_chunks(
        query,
        collection_name,
        embedding_model,
        ollama_url,
        top_k,
    )

    result = await generate_rag_response(query, retrieved_docs, llm_model, ollama_url)

    return {
        "query": query,
        "answer": result["answer"],
        "sources": result["sources"],
        "num_sources": len(result["sources"]),
        "collection_name": collection_name,
        "execution_id": ctx.execution_id,
    }


if __name__ == "__main__":  # pragma: no cover
    import json

    current_dir = Path(__file__).parent.parent
    docs_path = current_dir / "docs"

    if not docs_path.exists():
        print(f"Error: Sample docs not found at {docs_path}")
        print("Please create the docs directory with markdown files first.")
        exit(1)

    try:
        print("=" * 80)
        print("LangChain RAG Pipeline Demo (Chroma Vector Store)")
        print("=" * 80 + "\n")

        print("Step 1: Indexing documents...\n")
        result = rag_index_langchain.run(
            {
                "docs_path": str(docs_path),
                "collection_name": "flux_docs_langchain",
                "chunk_size": 500,
            },
        )

        if result.has_failed:
            raise Exception(f"Indexing failed: {result.output}")

        print(
            f"Indexed {result.output.get('num_chunks')} chunks from "
            f"{result.output.get('num_documents')} documents",
        )
        print(f"Collection: {result.output.get('collection_name')}")
        print(f"Persisted to: {result.output.get('persist_path')}\n")
        print("=" * 80 + "\n")

        print("Step 2: Query 1 - What are Flux workflows?\n")
        result = rag_query_langchain.run(
            {
                "collection_name": "flux_docs_langchain",
                "query": "What are Flux workflows?",
                "top_k": 3,
            },
        )

        if result.has_failed:
            raise Exception(f"Query failed: {result.output}")

        print(f"Answer: {result.output.get('answer')}\n")
        print(f"Sources: {json.dumps(result.output.get('sources'), indent=2)}\n")
        print("=" * 80 + "\n")

        print("Step 3: Query 2 - How does task caching work? (reusing index)\n")
        result = rag_query_langchain.run(
            {
                "collection_name": "flux_docs_langchain",
                "query": "How does task caching work in Flux?",
                "top_k": 3,
            },
        )

        if result.has_failed:
            raise Exception(f"Query failed: {result.output}")

        print(f"Answer: {result.output.get('answer')}\n")
        print(f"Sources: {json.dumps(result.output.get('sources'), indent=2)}\n")

        print("=" * 80)
        print("LangChain RAG pipeline working successfully!")
        print("Index reused for multiple queries (efficient!)")
        print("=" * 80)

    except Exception as e:
        print(f"Error: {e}")
        print("\nMake sure:")
        print("1. Ollama is running: ollama serve")
        print("2. Required models are pulled:")
        print("   ollama pull llama3")
        print("   ollama pull nomic-embed-text")
        print("3. Dependencies are installed:")
        print(
            "   pip install langchain-core langchain-ollama langchain-chroma langchain-community langchain-text-splitters",
        )
