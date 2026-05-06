"""A.4 T3 Part A: test query_insights_by_type() method."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
import asyncio


@pytest.mark.asyncio
async def test_query_insights_by_type_returns_list():
    """Test that query_insights_by_type returns correct structure from ChromaDB response."""
    # Import the mixin class
    from apps.backend.core._memory._chromadb import ChromaDbMixin
    
    # Create a minimal instance with the mixin
    instance = ChromaDbMixin()
    
    # Mock the ChromaDB collection
    mock_collection = MagicMock()
    instance._chroma_collection = mock_collection
    
    # Mock ChromaDB .get() response structure
    mock_collection.get.return_value = {
        "ids": ["id1", "id2"],
        "documents": ["text1", "text2"],
        "metadatas": [
            {"type": "warmup_candidate", "niche": "wall art", "score": "0.85"},
            {"type": "warmup_candidate", "niche": "posters", "score": "0.75"}
        ]
    }
    
    # Call the method
    result = await instance.query_insights_by_type("warmup_candidate", limit=50)
    
    # Assert correct structure
    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0] == {
        "id": "id1",
        "text": "text1",
        "metadata": {"type": "warmup_candidate", "niche": "wall art", "score": "0.85"}
    }
    assert result[1] == {
        "id": "id2",
        "text": "text2",
        "metadata": {"type": "warmup_candidate", "niche": "posters", "score": "0.75"}
    }
    
    # Verify .get() was called with correct parameters
    mock_collection.get.assert_called_once_with(
        where={"type": "warmup_candidate"},
        limit=50
    )


@pytest.mark.asyncio
async def test_query_insights_by_type_handles_exception():
    """Test that query_insights_by_type returns empty list on exception."""
    from apps.backend.core._memory._chromadb import ChromaDbMixin
    
    instance = ChromaDbMixin()
    mock_collection = MagicMock()
    instance._chroma_collection = mock_collection
    
    # Mock .get() to raise an exception
    mock_collection.get.side_effect = Exception("ChromaDB error")
    
    # Call the method
    result = await instance.query_insights_by_type("warmup_candidate")
    
    # Assert returns empty list
    assert result == []


@pytest.mark.asyncio
async def test_query_insights_by_type_returns_empty_if_no_collection():
    """Test that query_insights_by_type returns empty list if collection is None."""
    from apps.backend.core._memory._chromadb import ChromaDbMixin
    
    instance = ChromaDbMixin()
    instance._chroma_collection = None
    
    result = await instance.query_insights_by_type("warmup_candidate")
    
    assert result == []
