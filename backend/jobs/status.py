from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from backend.db import get_db
from backend.models import CrawlJob, Enrichment

router = APIRouter()

@router.get("/scrape/{job_id}")
def get_scrape_status(job_id: int, db: Session = Depends(get_db)):
    job = db.query(CrawlJob).filter_by(job_id=job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    response = {
        "job_id": job.job_id,
        "state": job.state,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "error": job.error,
        "updated_fields": None,
        "enrichment": None,
    }

    if job.state == "success":
        enrichment = db.query(Enrichment).filter_by(fsq_place_id=job.fsq_place_id).first()
        if enrichment:
            snap = enrichment.to_dict()
            response["enrichment"] = snap
            response["updated_fields"] = list(snap.keys())

    return response
