from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    conversation_id: int | None = None
    model: str = "auto"
    message: str = Field(min_length=1)
    system_prompt: str | None = None
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    agent_id: str | None = None
    tools_enabled: bool = False
    rag_enabled: bool = False
    images: list[str] = Field(default_factory=list)
    regenerate: bool = False
    web_enabled: bool = False


class InstallRequest(BaseModel):
    tag: str = Field(min_length=1)


class TagsRequest(BaseModel):
    tags: list[str] | None = None


class TagRequest(BaseModel):
    tag: str


class ConversationCreate(BaseModel):
    title: str = "Nouvelle conversation"
    model: str = ""


class TitleUpdate(BaseModel):
    title: str


class TTSRequest(BaseModel):
    text: str = Field(min_length=1)
    voice: str | None = None
    rate: int | None = Field(default=None, ge=50, le=400)
