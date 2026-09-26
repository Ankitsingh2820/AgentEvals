from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Agent, AgentVersion
from app.providers import available_providers, caching_mode
from app.schemas import AgentConfig, AgentCreate, AgentOut, AgentVersionOut
from app.tools import unknown_tools

router = APIRouter(prefix="/agents", tags=["agents"])


def _validate_config(config: AgentConfig) -> None:
    if config.provider not in available_providers():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"unknown provider '{config.provider}'; available: {available_providers()}",
        )
    if missing := unknown_tools(config.tools):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"unknown tools: {missing}")
    if config.options.prompt_caching and caching_mode(config.provider) == "automatic":
        # The switch would do nothing, so a version (or experiment) built on it would
        # claim a change that never happens.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"prompt caching is automatic for provider '{config.provider}' (long prompts are "
            "cached without asking); remove options.prompt_caching",
        )


def _new_version(agent: Agent, config: AgentConfig, number: int) -> AgentVersion:
    data = config.model_dump(include=set(AgentConfig.model_fields))
    data["metadata_"] = data.pop("metadata")
    version = AgentVersion(version=number, **data)
    agent.versions.append(version)  # appending via the parent cascades the session add
    return version


def get_agent_or_404(db: Session, agent_id: str) -> Agent:
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")
    return agent


@router.post("", response_model=AgentOut, status_code=status.HTTP_201_CREATED)
def create_agent(body: AgentCreate, db: Session = Depends(get_db)) -> Agent:
    _validate_config(body)
    if db.scalar(select(Agent).where(Agent.name == body.name)):
        raise HTTPException(status.HTTP_409_CONFLICT, "an agent with this name already exists")
    agent = Agent(name=body.name, description=body.description, tags=body.tags)
    db.add(agent)
    _new_version(agent, body, 1)
    db.commit()
    db.refresh(agent)
    return agent


@router.get("", response_model=list[AgentOut])
def list_agents(db: Session = Depends(get_db)) -> list[Agent]:
    return list(db.scalars(select(Agent).order_by(Agent.created_at.desc())))


@router.get("/{agent_id}", response_model=AgentOut)
def get_agent(agent_id: str, db: Session = Depends(get_db)) -> Agent:
    return get_agent_or_404(db, agent_id)


@router.post(
    "/{agent_id}/versions", response_model=AgentVersionOut, status_code=status.HTTP_201_CREATED
)
def create_version(agent_id: str, body: AgentConfig, db: Session = Depends(get_db)):
    """Change an agent's configuration. Creates a new immutable version; old runs keep
    pointing at the version they used."""
    agent = get_agent_or_404(db, agent_id)
    _validate_config(body)
    version = _new_version(agent, body, agent.latest_version.version + 1)
    db.commit()
    return version


@router.get("/{agent_id}/versions", response_model=list[AgentVersionOut])
def list_versions(agent_id: str, db: Session = Depends(get_db)):
    return get_agent_or_404(db, agent_id).versions
