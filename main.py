import os
import uuid
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure videos output directory exists
VIDEOS_DIR = os.path.join(os.getcwd(), "videos")
os.makedirs(VIDEOS_DIR, exist_ok=True)

# Mount static route to serve generated videos
app.mount("/videos", StaticFiles(directory=VIDEOS_DIR), name="videos")


class Scene(BaseModel):
    text_hi: str = Field(..., description="Hindi text to narrate for this scene")
    duration: float = Field(5.0, gt=1, le=60, description="Duration in seconds for the scene")
    mood: Optional[str] = Field("happy", description="Mood of the scene (affects avatar face)")


class GenerateRequest(BaseModel):
    title: Optional[str] = Field(None, description="Optional title for the video")
    scenes: List[Scene]


@app.get("/")
def read_root():
    return {"message": "Hindi Cartoon Video Generator Backend"}


@app.get("/api/hello")
def hello():
    return {"message": "Hello from the backend API!"}


@app.get("/test")
def test_database():
    """Test endpoint to check if database is available and accessible"""
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }

    try:
        from database import db

        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except ImportError:
        response["database"] = "❌ Database module not found (run enable-database first)"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    # Check environment variables
    import os as _os
    response["database_url"] = "✅ Set" if _os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if _os.getenv("DATABASE_NAME") else "❌ Not Set"

    return response


# ----------------- Cartoon Video Generation -----------------

def make_cartoon_face_png(path: str, mood: str = "happy", size: int = 400, color=(255, 224, 189)):
    """Create a simple cartoon face PNG using PIL and save to path."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Face circle
    cx, cy, r = size // 2, size // 2, int(size * 0.45)
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color, outline=(0, 0, 0), width=4)

    # Eyes
    eye_r = int(size * 0.05)
    eye_y = cy - int(size * 0.1)
    eye_dx = int(size * 0.15)
    draw.ellipse((cx - eye_dx - eye_r, eye_y - eye_r, cx - eye_dx + eye_r, eye_y + eye_r), fill=(0, 0, 0))
    draw.ellipse((cx + eye_dx - eye_r, eye_y - eye_r, cx + eye_dx + eye_r, eye_y + eye_r), fill=(0, 0, 0))

    # Mouth
    if mood == "sad":
        draw.arc((cx - int(size * 0.2), cy + int(size * 0.05), cx + int(size * 0.2), cy + int(size * 0.35)), 20, 160, fill=(0, 0, 0), width=5)
    else:
        draw.arc((cx - int(size * 0.2), cy + int(size * 0.0), cx + int(size * 0.2), cy + int(size * 0.3)), 200, 340, fill=(0, 0, 0), width=5)

    # Simple hair/top
    draw.rectangle((cx - int(size * 0.25), cy - r - int(size * 0.08), cx + int(size * 0.25), cy - r + int(size * 0.12)), fill=(60, 40, 30))

    img.save(path, "PNG")


def synthesize_tts(text: str, out_path: str, lang: str = "hi"):
    from gtts import gTTS

    tts = gTTS(text=text, lang=lang)
    tts.save(out_path)


def make_scene_clip(text_hi: str, duration: float, mood: str, face_png: str):
    from moviepy.editor import ColorClip, CompositeVideoClip, ImageClip

    width, height = 1280, 720
    # Background color varies by mood
    bg_color = (240, 255, 240) if mood == "happy" else (240, 240, 255)
    bg = ColorClip(size=(width, height), color=bg_color).set_duration(duration)

    avatar = ImageClip(face_png).set_duration(duration).resize(height=400)

    # Animate avatar left-to-right and back
    def pos_fn(t):
        # horizontal oscillation
        import math
        x = (width - 400) * (0.5 + 0.4 * math.sin(2 * math.pi * (t / max(duration, 0.1))))
        y = height * 0.35
        return (x, y)

    avatar = avatar.set_position(pos_fn)

    clip = CompositeVideoClip([bg, avatar]).set_duration(duration)
    return clip


@app.post("/api/generate")
def generate_video(req: GenerateRequest):
    if not req.scenes or len(req.scenes) == 0:
        raise HTTPException(status_code=400, detail="At least one scene is required")

    # Import moviepy types only when needed
    from moviepy.editor import AudioFileClip, concatenate_videoclips

    # Prepare resources
    temp_id = str(uuid.uuid4())
    face_png = os.path.join(VIDEOS_DIR, f"avatar_{temp_id}.png")
    make_cartoon_face_png(face_png, mood="happy")

    clips = []
    audio_paths = []

    try:
        for idx, sc in enumerate(req.scenes):
            # TTS for the scene in Hindi
            audio_path = os.path.join(VIDEOS_DIR, f"scene_{temp_id}_{idx}.mp3")
            synthesize_tts(sc.text_hi, audio_path, lang="hi")
            audio_paths.append(audio_path)
            scene_audio = AudioFileClip(audio_path)

            # If provided duration is shorter than audio, extend duration to audio duration
            duration = max(sc.duration, scene_audio.duration)
            clip = make_scene_clip(sc.text_hi, duration, sc.mood or "happy", face_png)
            clip = clip.set_audio(scene_audio)

            clips.append(clip)

        final = concatenate_videoclips(clips, method="compose")
        out_name = (req.title or "cartoon_video").strip().replace(" ", "_")
        out_name = f"{out_name}_{temp_id}.mp4"
        out_path = os.path.join(VIDEOS_DIR, out_name)

        # Write the video file. moviepy uses imageio-ffmpeg; ensure dependency installed.
        final.write_videofile(out_path, fps=24, codec="libx264", audio_codec="aac")

        # Cleanup clip objects
        for c in clips:
            c.close()
        final.close()

        # Return public URL
        return {
            "video_url": f"/videos/{out_name}",
            "file_name": out_name
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate video: {str(e)}")
    finally:
        # Clean temp image (keep audio for debugging until process ends)
        try:
            if os.path.exists(face_png):
                os.remove(face_png)
        except Exception:
            pass
