"""
RAG (Retrieval Augmented Generation) Agent using Ollama.

This example demonstrates a fully local RAG implementation using:
- Ollama for both embeddings (nomic-embed-text) and LLM (llama3)
- FAISS for fast vector similarity search
- Markdown documents as knowledge base

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

Usage:
    # Index documents from a directory
    flux workflow run rag_agent_ollama '{
        "mode": "index",
        "docs_path": "./examples/ai/docs",
        "chunk_size": 500
    }'

    # Query with the indexed knowledge base (resume with execution_id from index step)
    flux workflow resume rag_agent_ollama <execution_id> '{
        "mode": "query",
        "query": "What are Flux workflows?"
    }'

    # Use different models
    flux workflow resume rag_agent_ollama <execution_id> '{
        "mode": "query",
        "query": "How does scheduling work?",
        "llm_model": "qwen2.5:0.5b",
        "embedding_model": "nomic-embed-text"
    }'
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import faiss
import numpy as np
from ollama import AsyncClient

from flux import ExecutionContext, task, workflow


@task
async def load_markdown_documents(docs_path: str) -> list[dict[str, str]]:
    """
    Load markdown documents from a directory.

    Args:
        docs_path: Path to directory containing markdown files

    Returns:
        List of documents with content and metadata
    """
    docs_dir = Path(docs_path)

    if not docs_dir.exists():
        raise ValueError(f"Directory not found: {docs_path}")

    if not docs_dir.is_dir():
        raise ValueError(f"Path is not a directory: {docs_path}")

    documents = []
    md_files = list(docs_dir.glob("**/*.md"))

    if not md_files:
        raise ValueError(f"No markdown files found in: {docs_path}")

    for md_file in md_files:
        try:
            content = md_file.read_text(encoding="utf-8")
            documents.append(
                {
                    "content": content,
                    "filename": md_file.name,
                    "path": str(md_file.relative_to(docs_dir)),
                },
            )
        except Exception as e:
            # Log but continue if a single file fails
            print(f"Warning: Failed to read {md_file}: {e}")

    return documents


@task
async def chunk_documents(
    documents: list[dict[str, str]],
    chunk_size: int = 500,
    overlap: int = 50,
) -> list[dict[str, Any]]:
    """
    Split documents into smaller chunks with overlap.

    Args:
        documents: List of documents with content and metadata
        chunk_size: Maximum characters per chunk
        overlap: Character overlap between chunks

    Returns:
        List of chunks with content and metadata
    """
    chunks = []

    for doc in documents:
        content = doc["content"]
        filename = doc["filename"]
        path = doc["path"]

        # Split content into chunks
        start = 0
        chunk_idx = 0

        while start < len(content):
            end = start + chunk_size
            chunk_text = content[start:end]

            # Only add non-empty chunks
            if chunk_text.strip():
                chunks.append(
                    {
                        "content": chunk_text,
                        "filename": filename,
                        "path": path,
                        "chunk_index": chunk_idx,
                        "start_char": start,
                        "end_char": min(end, len(content)),
                    },
                )
                chunk_idx += 1

            start = end - overlap

    return chunks


@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=120)
async def generate_embeddings(
    texts: list[str],
    model: str = "nomic-embed-text",
    ollama_url: str = "http://localhost:11434",
) -> np.ndarray:
    """
    Generate embeddings for text chunks using Ollama.

    Args:
        texts: List of text strings to embed
        model: Ollama embedding model to use
        ollama_url: Ollama server URL

    Returns:
        NumPy array of embeddings (shape: [num_texts, embedding_dim])
    """
    try:
        client = AsyncClient(host=ollama_url)
        embeddings = []

        # Generate embeddings for each text
        for text in texts:
            response = await client.embeddings(model=model, prompt=text)
            embeddings.append(response["embedding"])

        return np.array(embeddings, dtype=np.float32)

    except Exception as e:
        raise RuntimeError(
            f"Failed to generate embeddings: {str(e)}. "
            f"Make sure Ollama is running and model '{model}' is available. "
            f"Run: ollama pull {model}",
        ) from e


@task
async def build_faiss_index(embeddings: np.ndarray) -> bytes:
    """
    Build FAISS index from embeddings.

    Args:
        embeddings: NumPy array of embeddings

    Returns:
        Serialized FAISS index (as bytes for Flux state management)
    """
    dimension = embeddings.shape[1]

    # Create a simple flat (brute-force) index for exact search
    index = faiss.IndexFlatL2(dimension)

    # Add embeddings to index
    index.add(embeddings)

    # Serialize index to bytes
    index_bytes = faiss.serialize_index(index).tobytes()

    return index_bytes


@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=120)
async def retrieve_relevant_chunks(
    query: str,
    index_bytes: bytes,
    chunks: list[dict[str, Any]],
    embedding_model: str,
    ollama_url: str,
    top_k: int = 3,
) -> list[dict[str, Any]]:
    """
    Retrieve most relevant chunks for a query using semantic search.

    Args:
        query: User's question
        index_bytes: Serialized FAISS index
        chunks: List of document chunks
        embedding_model: Ollama embedding model
        ollama_url: Ollama server URL
        top_k: Number of chunks to retrieve

    Returns:
        List of most relevant chunks with similarity scores
    """
    # Deserialize FAISS index
    index = faiss.deserialize_index(np.frombuffer(index_bytes, dtype=np.uint8))

    # Generate query embedding
    query_embedding = await generate_embeddings([query], embedding_model, ollama_url)

    # Search for similar chunks
    distances, indices = index.search(query_embedding, top_k)

    # Build results with metadata
    results = []
    for idx, distance in zip(indices[0], distances[0]):
        chunk = chunks[idx]
        results.append(
            {
                "content": chunk["content"],
                "filename": chunk["filename"],
                "path": chunk["path"],
                "chunk_index": chunk["chunk_index"],
                "similarity_score": float(distance),
            },
        )

    return results


@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=60)
async def generate_rag_response(
    query: str,
    context_chunks: list[dict[str, Any]],
    model: str,
    ollama_url: str,
) -> dict[str, Any]:
    """
    Generate response using LLM with retrieved context.

    Args:
        query: User's question
        context_chunks: Retrieved relevant chunks
        model: Ollama LLM model to use
        ollama_url: Ollama server URL

    Returns:
        Dictionary with response and sources
    """
    try:
        client = AsyncClient(host=ollama_url)

        # Format context from retrieved chunks
        context_parts = []
        for i, chunk in enumerate(context_chunks, 1):
            context_parts.append(f"[Source {i}: {chunk['filename']}]\n{chunk['content'].strip()}")

        context = "\n\n".join(context_parts)

        # Create prompt with context
        prompt = f"""Context from documentation:

{context}

Question: {query}

Answer the question based on the context provided above. If the context doesn't contain relevant information, say so clearly. Be concise and accurate."""

        # Generate response
        response = await client.chat(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful assistant that answers questions based on provided documentation context. Always cite your sources.",
                },
                {"role": "user", "content": prompt},
            ],
        )

        answer = response["message"]["content"]

        # Include sources in response
        sources = [
            {"filename": chunk["filename"], "path": chunk["path"]} for chunk in context_chunks
        ]

        return {
            "answer": answer,
            "sources": sources,
            "context_chunks": context_chunks,
        }

    except Exception as e:
        raise RuntimeError(
            f"Failed to generate response: {str(e)}. "
            f"Make sure Ollama is running and model '{model}' is available. "
            f"Run: ollama pull {model}",
        ) from e


@workflow.with_options(name="rag_index_documents")
async def rag_index_documents(ctx: ExecutionContext[dict[str, Any]]):
    """
    Index documents and save to disk for later querying.

    This workflow builds a vector index from markdown documents and saves it for reuse.
    Use this once to index your documents, then use rag_query_documents for queries.

    Input format:
    {
        "docs_path": "./path/to/docs",           # Required: path to markdown docs
        "index_name": "my_docs",                 # Required: unique name for this index
        "chunk_size": 500,                       # Optional: chars per chunk
        "overlap": 50,                           # Optional: chunk overlap
        "embedding_model": "nomic-embed-text",   # Optional: embedding model
        "ollama_url": "http://localhost:11434"   # Optional: Ollama server URL
    }

    Returns:
        Dictionary with indexing results and index location
    """
    input_data = ctx.input or {}

    # Required parameters
    docs_path = input_data.get("docs_path")
    index_name = input_data.get("index_name")

    if not docs_path:
        return {
            "error": "Missing required parameter 'docs_path'",
            "execution_id": ctx.execution_id,
        }

    if not index_name:
        return {
            "error": "Missing required parameter 'index_name'",
            "execution_id": ctx.execution_id,
        }

    # Configuration
    chunk_size = input_data.get("chunk_size", 500)
    overlap = input_data.get("overlap", 50)
    embedding_model = input_data.get("embedding_model", "nomic-embed-text")
    ollama_url = input_data.get("ollama_url", "http://localhost:11434")

    # Step 1: Load markdown documents
    documents = await load_markdown_documents(docs_path)

    # Step 2: Chunk documents
    chunks = await chunk_documents(documents, chunk_size, overlap)

    if not chunks:
        return {
            "error": "No chunks created from documents",
            "execution_id": ctx.execution_id,
        }

    # Step 3: Generate embeddings
    chunk_texts = [chunk["content"] for chunk in chunks]
    embeddings = await generate_embeddings(chunk_texts, embedding_model, ollama_url)

    # Step 4: Build FAISS index
    index_bytes = await build_faiss_index(embeddings)

    # Step 5: Save index and chunks to disk
    import pickle
    from pathlib import Path

    # Save to .flux directory
    index_dir = Path.home() / ".flux" / "rag_indexes"
    index_dir.mkdir(parents=True, exist_ok=True)

    index_file = index_dir / f"{index_name}_index.faiss"
    chunks_file = index_dir / f"{index_name}_chunks.pkl"
    metadata_file = index_dir / f"{index_name}_metadata.json"

    # Save FAISS index
    index_file.write_bytes(index_bytes)

    # Save chunks
    with open(chunks_file, "wb") as f:
        pickle.dump(chunks, f)

    # Save metadata
    import json

    metadata = {
        "index_name": index_name,
        "docs_path": str(docs_path),
        "num_documents": len(documents),
        "num_chunks": len(chunks),
        "chunk_size": chunk_size,
        "overlap": overlap,
        "embedding_model": embedding_model,
        "created_at": str(documents[0] if documents else ""),
    }

    with open(metadata_file, "w") as f:
        json.dump(metadata, f, indent=2)

    return {
        "status": "indexed",
        "index_name": index_name,
        "index_file": str(index_file),
        "num_documents": len(documents),
        "num_chunks": len(chunks),
        "embedding_model": embedding_model,
        "execution_id": ctx.execution_id,
    }


@workflow.with_options(name="rag_query_documents")
async def rag_query_documents(ctx: ExecutionContext[dict[str, Any]]):
    """
    Query pre-indexed documents using RAG.

    This workflow loads a previously built index and answers questions.
    Run rag_index_documents first to create the index.

    Input format:
    {
        "index_name": "my_docs",                 # Required: name of pre-built index
        "query": "Your question here",           # Required: question to answer
        "llm_model": "llama3",                   # Optional: LLM model
        "top_k": 3,                             # Optional: chunks to retrieve
        "ollama_url": "http://localhost:11434"   # Optional: Ollama server URL
    }

    Returns:
        Dictionary with answer, sources, and metadata
    """
    input_data = ctx.input or {}

    # Required parameters
    index_name = input_data.get("index_name")
    query = input_data.get("query")

    if not index_name:
        return {
            "error": "Missing required parameter 'index_name'",
            "execution_id": ctx.execution_id,
        }

    if not query:
        return {
            "error": "Missing required parameter 'query'",
            "execution_id": ctx.execution_id,
        }

    # Configuration
    llm_model = input_data.get("llm_model", "llama3")
    top_k = input_data.get("top_k", 3)
    ollama_url = input_data.get("ollama_url", "http://localhost:11434")

    # Load index and chunks from disk
    import json
    import pickle
    from pathlib import Path

    index_dir = Path.home() / ".flux" / "rag_indexes"
    index_file = index_dir / f"{index_name}_index.faiss"
    chunks_file = index_dir / f"{index_name}_chunks.pkl"
    metadata_file = index_dir / f"{index_name}_metadata.json"

    # Check if index exists
    if not index_file.exists():
        return {
            "error": f"Index '{index_name}' not found. Run rag_index_documents first.",
            "execution_id": ctx.execution_id,
        }

    # Load FAISS index
    index_bytes = index_file.read_bytes()

    # Load chunks
    with open(chunks_file, "rb") as f:
        chunks = pickle.load(f)

    # Load metadata
    with open(metadata_file) as f:
        metadata = json.load(f)

    embedding_model = metadata.get("embedding_model", "nomic-embed-text")

    # Retrieve relevant chunks
    relevant_chunks = await retrieve_relevant_chunks(
        query,
        index_bytes,
        chunks,
        embedding_model,
        ollama_url,
        top_k,
    )

    # Generate response with context
    result = await generate_rag_response(query, relevant_chunks, llm_model, ollama_url)

    return {
        "query": query,
        "answer": result["answer"],
        "sources": result["sources"],
        "num_sources": len(result["sources"]),
        "index_name": index_name,
        "num_chunks_indexed": metadata.get("num_chunks", 0),
        "execution_id": ctx.execution_id,
    }


if __name__ == "__main__":  # pragma: no cover
    import json

    # Get the docs path relative to this file
    current_dir = Path(__file__).parent
    docs_path = current_dir / "docs"

    if not docs_path.exists():
        print(f"Error: Sample docs not found at {docs_path}")
        print("Please create the docs directory with markdown files first.")
        exit(1)

    try:
        print("=" * 80)
        print("RAG Agent Demo - Two-Workflow Pattern (Production-Ready)")
        print("=" * 80 + "\n")

        # Step 1: Index documents once
        print("Step 1: Indexing documents...\n")
        result = rag_index_documents.run(
            {
                "docs_path": str(docs_path),
                "index_name": "flux_docs",
                "chunk_size": 500,
            },
        )

        if result.has_failed:
            raise Exception(f"Indexing failed: {result.output}")

        print(
            f"✓ Indexed {result.output.get('num_chunks')} chunks from {result.output.get('num_documents')} documents",
        )
        print(f"✓ Index saved as: {result.output.get('index_name')}\n")
        print("=" * 80 + "\n")

        # Step 2: Query 1
        print("Step 2: Query 1 - What are Flux workflows?\n")
        result = rag_query_documents.run(
            {
                "index_name": "flux_docs",
                "query": "What are Flux workflows?",
                "top_k": 3,
            },
        )

        if result.has_failed:
            raise Exception(f"Query failed: {result.output}")

        print(f"Answer: {result.output.get('answer')}\n")
        print(f"Sources: {json.dumps(result.output.get('sources'), indent=2)}\n")
        print("=" * 80 + "\n")

        # Step 3: Query 2 (reusing the same index - much faster!)
        print("Step 3: Query 2 - How does task caching work? (reusing index)\n")
        result = rag_query_documents.run(
            {
                "index_name": "flux_docs",
                "query": "How does task caching work in Flux?",
                "top_k": 3,
            },
        )

        if result.has_failed:
            raise Exception(f"Query failed: {result.output}")

        print(f"Answer: {result.output.get('answer')}\n")
        print(f"Sources: {json.dumps(result.output.get('sources'), indent=2)}\n")

        print("=" * 80)
        print("✓ RAG agent working successfully!")
        print("✓ Index reused for multiple queries (efficient!)")
        print("=" * 80)

    except Exception as e:
        print(f"Error: {e}")
        print("\nMake sure:")
        print("1. Ollama is running: ollama serve")
        print("2. Required models are pulled:")
        print("   ollama pull llama3")
        print("   ollama pull nomic-embed-text")
