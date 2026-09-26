"""Datasets are immutable. Posting a dataset with an existing name creates its next
version; the old version (and every evaluation that used it) is untouched."""

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Dataset, DatasetCase
from app.schemas import DatasetCaseIn, DatasetDetailOut, DatasetIn, DatasetOut

router = APIRouter(prefix="/datasets", tags=["datasets"])

MAX_JSONL_BYTES = 20 * 1024 * 1024


def _out(dataset: Dataset, detail: bool = False):
    data = {
        "id": dataset.id,
        "name": dataset.name,
        "version": dataset.version,
        "description": dataset.description,
        "created_at": dataset.created_at,
        "case_count": len(dataset.cases),
    }
    if detail:
        return DatasetDetailOut(**data, cases=dataset.cases)
    return DatasetOut(**data)


def _create(db: Session, body: DatasetIn) -> Dataset:
    keys = [c.id or f"case_{i + 1:03d}" for i, c in enumerate(body.cases)]
    duplicates = sorted({k for k in keys if keys.count(k) > 1})
    if duplicates:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"duplicate case ids: {duplicates}"
        )
    latest = db.scalar(select(func.max(Dataset.version)).where(Dataset.name == body.name))
    dataset = Dataset(name=body.name, description=body.description, version=(latest or 0) + 1)
    for i, (key, case) in enumerate(zip(keys, body.cases, strict=True)):
        dataset.cases.append(
            DatasetCase(
                case_key=key,
                position=i,
                input=case.input,
                expected_output=case.expected_output,
                labels=case.labels,
                metadata_=case.metadata,
            )
        )
    db.add(dataset)
    db.commit()
    return dataset


@router.post("", response_model=DatasetDetailOut, status_code=status.HTTP_201_CREATED)
def create_dataset(body: DatasetIn, db: Session = Depends(get_db)):
    return _out(_create(db, body), detail=True)


@router.post(
    "/jsonl",
    response_model=DatasetOut,
    status_code=status.HTTP_201_CREATED,
    openapi_extra={"requestBody": {"content": {"application/x-ndjson": {}}}},
)
async def upload_jsonl(
    request: Request,
    name: str = Query(min_length=1, max_length=200),
    description: str | None = None,
    db: Session = Depends(get_db),
):
    """Upload cases as JSON Lines: one case object per line, same fields as POST /datasets."""
    raw = await request.body()
    if len(raw) > MAX_JSONL_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "dataset too large")
    cases, errors = [], []
    for lineno, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            cases.append(DatasetCaseIn.model_validate(json.loads(line)))
        except (json.JSONDecodeError, ValidationError) as e:
            errors.append(f"line {lineno}: {str(e).splitlines()[0]}")
    if errors:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, errors[:20])
    try:
        body = DatasetIn(name=name, description=description, cases=cases)
    except ValidationError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    return _out(_create(db, body))


@router.get("", response_model=list[DatasetOut])
def list_datasets(db: Session = Depends(get_db)):
    datasets = db.scalars(select(Dataset).order_by(Dataset.name, Dataset.version.desc()))
    return [_out(d) for d in datasets]


@router.get("/{dataset_id}", response_model=DatasetDetailOut)
def get_dataset(dataset_id: str, db: Session = Depends(get_db)):
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dataset not found")
    return _out(dataset, detail=True)
