import json
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form, BackgroundTasks
from pydantic import BaseModel, Field, HttpUrl
from utils.auth_utils import get_current_user_id_from_jwt
from services.supabase import DBConnection
from knowledge_base.file_processor import FileProcessor
from utils.logger import logger
from flags.flags import is_enabled

router = APIRouter(prefix="/knowledge-base", tags=["knowledge-base"])

class KnowledgeBaseEntry(BaseModel):
    entry_id: Optional[str] = None
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    content: str = Field(..., min_length=1)
    usage_context: str = Field(default="always", pattern="^(always|on_request|contextual)$")
    is_active: bool = True

class KnowledgeBaseEntryResponse(BaseModel):
    entry_id: str
    name: str
    description: Optional[str]
    content: str
    usage_context: str
    is_active: bool
    content_tokens: Optional[int]
    created_at: str
    updated_at: str
    source_type: Optional[str] = None
    source_metadata: Optional[dict] = None
    file_size: Optional[int] = None
    file_mime_type: Optional[str] = None

class KnowledgeBaseListResponse(BaseModel):
    entries: List[KnowledgeBaseEntryResponse]
    total_count: int
    total_tokens: int

class CreateKnowledgeBaseEntryRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    content: str = Field(..., min_length=1)
    usage_context: str = Field(default="always", pattern="^(always|on_request|contextual)$")

class UpdateKnowledgeBaseEntryRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    content: Optional[str] = Field(None, min_length=1)
    usage_context: Optional[str] = Field(None, pattern="^(always|on_request|contextual)$")
    is_active: Optional[bool] = None

class GitRepositoryRequest(BaseModel):
    git_url: HttpUrl
    branch: str = "main"
    include_patterns: Optional[List[str]] = None
    exclude_patterns: Optional[List[str]] = None

class ProcessingJobResponse(BaseModel):
    job_id: str
    job_type: str
    status: str
    source_info: dict
    result_info: dict
    entries_created: int
    total_files: int
    created_at: str
    completed_at: Optional[str]
    error_message: Optional[str]

db = DBConnection()

@router.get("/threads/{thread_id}", response_model=KnowledgeBaseListResponse)
async def get_thread_knowledge_base(
    thread_id: str,
    include_inactive: bool = False,
    user_id: str = Depends(get_current_user_id_from_jwt)
):
    if not await is_enabled("knowledge_base"):
        raise HTTPException(
            status_code=403, 
            detail="This feature is not available at the moment."
        )
    
    """Get all knowledge base entries for a thread"""
    try:
        client = await db.client

        thread_result = await client.table('threads').select('*').eq('thread_id', thread_id).execute()
        if not thread_result.data:
            raise HTTPException(status_code=404, detail="Thread not found")

        result = await client.rpc('get_thread_knowledge_base', {
            'p_thread_id': thread_id,
            'p_include_inactive': include_inactive
        }).execute()
        
        entries = []
        total_tokens = 0
        
        for entry_data in result.data or []:
            entry = KnowledgeBaseEntryResponse(
                entry_id=entry_data['entry_id'],
                name=entry_data['name'],
                description=entry_data['description'],
                content=entry_data['content'],
                usage_context=entry_data['usage_context'],
                is_active=entry_data['is_active'],
                content_tokens=entry_data.get('content_tokens'),
                created_at=entry_data['created_at'],
                updated_at=entry_data.get('updated_at', entry_data['created_at'])
            )
            entries.append(entry)
            total_tokens += entry_data.get('content_tokens', 0) or 0
        
        return KnowledgeBaseListResponse(
            entries=entries,
            total_count=len(entries),
            total_tokens=total_tokens
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting knowledge base for thread {thread_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve knowledge base")


@router.get("/agents/{agent_id}", response_model=KnowledgeBaseListResponse)
async def get_agent_knowledge_base(
    agent_id: str,
    include_inactive: bool = False,
    user_id: str = Depends(get_current_user_id_from_jwt)
):
    if not await is_enabled("knowledge_base"):
        raise HTTPException(
            status_code=403, 
            detail="This feature is not available at the moment."
        )
    
    """Get all knowledge base entries for an agent"""
    try:
        client = await db.client

        agent_result = await client.table('agents').select('*').eq('agent_id', agent_id).eq('account_id', user_id).execute()
        if not agent_result.data:
            raise HTTPException(status_code=404, detail="Agent not found or access denied")

        result = await client.rpc('get_agent_knowledge_base', {
            'p_agent_id': agent_id,
            'p_include_inactive': include_inactive
        }).execute()
        
        entries = []
        total_tokens = 0
        
        for entry_data in result.data or []:
            entry = KnowledgeBaseEntryResponse(
                entry_id=entry_data['entry_id'],
                name=entry_data['name'],
                description=entry_data['description'],
                content=entry_data['content'],
                usage_context=entry_data['usage_context'],
                is_active=entry_data['is_active'],
                content_tokens=entry_data.get('content_tokens'),
                created_at=entry_data['created_at'],
                updated_at=entry_data.get('updated_at', entry_data['created_at']),
                source_type=entry_data.get('source_type'),
                source_metadata=entry_data.get('source_metadata'),
                file_size=entry_data.get('file_size'),
                file_mime_type=entry_data.get('file_mime_type')
            )
            entries.append(entry)
            total_tokens += entry_data.get('content_tokens', 0) or 0
        
        return KnowledgeBaseListResponse(
            entries=entries,
            total_count=len(entries),
            total_tokens=total_tokens
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting knowledge base for agent {agent_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve agent knowledge base")

@router.post("/agents/{agent_id}", response_model=KnowledgeBaseEntryResponse)
async def create_agent_knowledge_base_entry(
    agent_id: str,
    entry_data: CreateKnowledgeBaseEntryRequest,
    user_id: str = Depends(get_current_user_id_from_jwt)
):
    if not await is_enabled("knowledge_base"):
        raise HTTPException(
            status_code=403, 
            detail="This feature is not available at the moment."
        )
    
    """Create a new knowledge base entry for an agent"""
    try:
        client = await db.client
        
        agent_result = await client.table('agents').select('account_id').eq('agent_id', agent_id).eq('account_id', user_id).execute()
        if not agent_result.data:
            raise HTTPException(status_code=404, detail="Agent not found or access denied")
        
        account_id = agent_result.data[0]['account_id']
        
        insert_data = {
            'agent_id': agent_id,
            'account_id': account_id,
            'name': entry_data.name,
            'description': entry_data.description,
            'content': entry_data.content,
            'usage_context': entry_data.usage_context
        }
        
        result = await client.table('agent_knowledge_base_entries').insert(insert_data).execute()
        
        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to create agent knowledge base entry")
        
        created_entry = result.data[0]
        
        return KnowledgeBaseEntryResponse(
            entry_id=created_entry['entry_id'],
            name=created_entry['name'],
            description=created_entry['description'],
            content=created_entry['content'],
            usage_context=created_entry['usage_context'],
            is_active=created_entry['is_active'],
            content_tokens=created_entry.get('content_tokens'),
            created_at=created_entry['created_at'],
            updated_at=created_entry['updated_at']
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating knowledge base entry for agent {agent_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to create agent knowledge base entry")

@router.post("/agents/{agent_id}/upload-file")
async def upload_file_to_agent_kb(
    agent_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user_id_from_jwt)
):
    if not await is_enabled("knowledge_base"):
        raise HTTPException(
            status_code=403, 
            detail="This feature is not available at the moment."
        )
    
    """Upload and process a file for agent knowledge base"""
    try:
        client = await db.client
        
        agent_result = await client.table('agents').select('account_id').eq('agent_id', agent_id).eq('account_id', user_id).execute()
        if not agent_result.data:
            raise HTTPException(status_code=404, detail="Agent not found or access denied")
        
        account_id = agent_result.data[0]['account_id']
        
        file_content = await file.read()
        job_id = await client.rpc('create_agent_kb_processing_job', {
            'p_agent_id': agent_id,
            'p_account_id': account_id,
            'p_job_type': 'file_upload',
            'p_source_info': {
                'filename': file.filename,
                'mime_type': file.content_type,
                'file_size': len(file_content)
            }
        }).execute()
        
        if not job_id.data:
            raise HTTPException(status_code=500, detail="Failed to create processing job")
        
        job_id = job_id.data
        background_tasks.add_task(
            process_file_background,
            job_id,
            agent_id,
            account_id,
            file_content,
            file.filename,
            file.content_type or 'application/octet-stream'
        )
        
        return {
            "job_id": job_id,
            "message": "File upload started. Processing in background.",
            "filename": file.filename
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading file to agent {agent_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to upload file")


@router.get("/agents/{agent_id}/processing-jobs", response_model=List[ProcessingJobResponse])
async def get_agent_processing_jobs(
    agent_id: str,
    limit: int = 10,
    user_id: str = Depends(get_current_user_id_from_jwt)
):
    if not await is_enabled("knowledge_base"):
        raise HTTPException(
            status_code=403, 
            detail="This feature is not available at the moment."
        )
    
    """Get processing jobs for an agent"""
    try:
        client = await db.client

        agent_result = await client.table('agents').select('account_id').eq('agent_id', agent_id).eq('account_id', user_id).execute()
        if not agent_result.data:
            raise HTTPException(status_code=404, detail="Agent not found or access denied")
        
        result = await client.rpc('get_agent_kb_processing_jobs', {
            'p_agent_id': agent_id,
            'p_limit': limit
        }).execute()
        
        jobs = []
        for job_data in result.data or []:
            job = ProcessingJobResponse(
                job_id=job_data['job_id'],
                job_type=job_data['job_type'],
                status=job_data['status'],
                source_info=job_data['source_info'],
                result_info=job_data['result_info'],
                entries_created=job_data['entries_created'],
                total_files=job_data['total_files'],
                created_at=job_data['created_at'],
                completed_at=job_data.get('completed_at'),
                error_message=job_data.get('error_message')
            )
            jobs.append(job)
        
        return jobs
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting processing jobs for agent {agent_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get processing jobs")

async def process_file_background(
    job_id: str,
    agent_id: str,
    account_id: str,
    file_content: bytes,
    filename: str,
    mime_type: str
):
    """Background task to process uploaded files"""
    
    processor = FileProcessor()
    client = await processor.db.client
    try:
        await client.rpc('update_agent_kb_job_status', {
            'p_job_id': job_id,
            'p_status': 'processing'
        }).execute()
        
        result = await processor.process_file_upload(
            agent_id, account_id, file_content, filename, mime_type
        )
        
        if result['success']:
            await client.rpc('update_agent_kb_job_status', {
                'p_job_id': job_id,
                'p_status': 'completed',
                'p_result_info': result,
                'p_entries_created': 1,
                'p_total_files': 1
            }).execute()
        else:
            await client.rpc('update_agent_kb_job_status', {
                'p_job_id': job_id,
                'p_status': 'failed',
                'p_error_message': result.get('error', 'Unknown error')
            }).execute()
            
    except Exception as e:
        logger.error(f"Error in background file processing for job {job_id}: {str(e)}")
        try:
            await client.rpc('update_agent_kb_job_status', {
                'p_job_id': job_id,
                'p_status': 'failed',
                'p_error_message': str(e)
            }).execute()
        except:
            pass


@router.get("/agents/{agent_id}/context")
async def get_agent_knowledge_base_context(
    agent_id: str,
    max_tokens: int = 4000,
    user_id: str = Depends(get_current_user_id_from_jwt)
):
    if not await is_enabled("knowledge_base"):
        raise HTTPException(
            status_code=403, 
            detail="This feature is not available at the moment."
        )
    
    """Get knowledge base context for agent prompts"""
    try:
        client = await db.client
        
        agent_result = await client.table('agents').select('agent_id').eq('agent_id', agent_id).eq('account_id', user_id).execute()
        if not agent_result.data:
            raise HTTPException(status_code=404, detail="Agent not found or access denied")
        
        result = await client.rpc('get_agent_knowledge_base_context', {
            'p_agent_id': agent_id,
            'p_max_tokens': max_tokens
        }).execute()
        
        context = result.data if result.data else None
        
        return {
            "context": context,
            "max_tokens": max_tokens,
            "agent_id": agent_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting knowledge base context for agent {agent_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve agent knowledge base context")


@router.get("/threads/{thread_id}/context")
async def get_knowledge_base_context(
    thread_id: str,
    max_tokens: int = 4000,
    user_id: str = Depends(get_current_user_id_from_jwt)
):
    if not await is_enabled("knowledge_base"):
        raise HTTPException(
            status_code=403, 
            detail="This feature is not available at the moment."
        )
    
    """Get knowledge base context for agent prompts"""
    try:
        client = await db.client
        thread_result = await client.table('threads').select('thread_id').eq('thread_id', thread_id).execute()
        if not thread_result.data:
            raise HTTPException(status_code=404, detail="Thread not found")
        
        result = await client.rpc('get_knowledge_base_context', {
            'p_thread_id': thread_id,
            'p_max_tokens': max_tokens
        }).execute()
        
        context = result.data if result.data else None
        
        return {
            "context": context,
            "max_tokens": max_tokens,
            "thread_id": thread_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting knowledge base context for thread {thread_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve knowledge base context")

@router.get("/threads/{thread_id}/combined-context")
async def get_combined_knowledge_base_context(
    thread_id: str,
    agent_id: Optional[str] = None,
    max_tokens: int = 4000,
    user_id: str = Depends(get_current_user_id_from_jwt)
):
    if not await is_enabled("knowledge_base"):
        raise HTTPException(
            status_code=403, 
            detail="This feature is not available at the moment."
        )
    
    """Get combined knowledge base context from both thread and agent sources"""
    try:
        client = await db.client
        thread_result = await client.table('threads').select('thread_id').eq('thread_id', thread_id).execute()
        if not thread_result.data:
            raise HTTPException(status_code=404, detail="Thread not found")
        
        result = await client.rpc('get_combined_knowledge_base_context', {
            'p_thread_id': thread_id,
            'p_agent_id': agent_id,
            'p_max_tokens': max_tokens
        }).execute()
        
        context = result.data if result.data else None
        
        return {
            "context": context,
            "max_tokens": max_tokens,
            "thread_id": thread_id,
            "agent_id": agent_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting combined knowledge base context for thread {thread_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve combined knowledge base context") 