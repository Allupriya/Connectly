"""File upload + serving backed by MongoDB GridFS (no local disk required).

Uploaded files are stored in the ``uploads`` GridFS bucket instead of on disk,
so the app works on hosts with an ephemeral filesystem (e.g. Render, where any
file written to disk is lost on the next deploy/restart). An upload returns a
``/uploads/<file_id>`` URL, and the GET route streams that file straight back
out of the database.

Security:
- Uploads are capped at ``settings.MAX_UPLOAD_MB``.
- Only real images are served *inline*; everything else is served as a download
  with ``application/octet-stream`` + ``nosniff`` so a malicious upload (HTML,
  SVG, JS, ...) can never execute as script in the app's origin.
"""

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from gridfs.errors import NoFile

from ..config import get_logger, settings
from ..db import get_fs
from .users import get_current_user

logger = get_logger(__name__)

router = APIRouter(tags=["Files"])

# Content types we consider safe to display inline in the browser. Note SVG is
# deliberately excluded (it can contain scripts) -> it downloads instead.
_INLINE_IMAGE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/bmp",
}


@router.post("/api/upload", summary="Upload a file or image")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    current=Depends(get_current_user),
):
    try:
        # Reject oversized uploads early using the declared Content-Length.
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > settings.max_upload_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File too large (max {settings.MAX_UPLOAD_MB} MB)",
            )

        contents = await file.read()
        if len(contents) > settings.max_upload_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File too large (max {settings.MAX_UPLOAD_MB} MB)",
            )
        if not contents:
            raise HTTPException(status_code=400, detail="Empty file")

        fs = get_fs()
        grid_in = fs.open_upload_stream(
            file.filename or "file",
            metadata={"content_type": file.content_type},
        )
        await grid_in.write(contents)
        await grid_in.close()
        file_id = str(grid_in._id)

        logger.info(
            "Upload OK: user %s stored '%s' as %s (%d bytes) in GridFS.",
            current.get("_id"),
            file.filename,
            file_id,
            len(contents),
        )
        return {
            "url": f"/uploads/{file_id}",
            "name": file.filename,
            "content_type": file.content_type,
        }
    except HTTPException:
        raise
    except Exception:
        logger.exception("Upload failed for file '%s'.", getattr(file, "filename", None))
        raise HTTPException(status_code=500, detail="File upload failed")


@router.get("/uploads/{file_id}", summary="View / download an uploaded file")
async def get_upload(file_id: str):
    try:
        try:
            oid = ObjectId(file_id)
        except (InvalidId, TypeError):
            logger.warning("Rejected invalid file id: %r", file_id)
            raise HTTPException(status_code=400, detail="Invalid file id")

        fs = get_fs()
        try:
            grid_out = await fs.open_download_stream(oid)
        except NoFile:
            logger.warning("File not found in GridFS: %s", file_id)
            raise HTTPException(status_code=404, detail="File not found")

        data = await grid_out.read()
        stored_type = (grid_out.metadata or {}).get("content_type") or ""
        # Quotes/newlines would break the header; strip them from the filename.
        safe_name = (grid_out.filename or file_id).replace('"', "").replace("\n", "")

        if stored_type in _INLINE_IMAGE_TYPES:
            # Real image -> safe to show inline with its true type.
            content_type = stored_type
            disposition = f'inline; filename="{safe_name}"'
        else:
            # Anything else -> force a download and a neutral type so it can
            # never run as script in our origin.
            content_type = "application/octet-stream"
            disposition = f'attachment; filename="{safe_name}"'

        headers = {
            "Content-Disposition": disposition,
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=31536000",
        }
        return Response(content=data, media_type=content_type, headers=headers)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to serve file %s.", file_id)
        raise HTTPException(status_code=500, detail="Failed to load file")
