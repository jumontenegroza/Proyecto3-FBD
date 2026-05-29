from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient
from datetime import datetime
from uuid import uuid4
import os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

client = MongoClient(os.environ["MONGO_URI"])
db = client["ISIS2304D31202610"]


@app.get("/")
def inicio():
    return {"estado": "API Dann-Alpes funcionando correctamente"}


# ── GET /api/hoteles/{hotel_id}/resenas ─────────────────────────
# Retorna todas las reseñas publicadas del hotel
@app.get('/api/hoteles/{hotel_id}/resenas')
def get_resenas(hotel_id: str):
    resenas = list(db["reseñas"].find(
        {"hotel_id": hotel_id, "estado": "publicada"},
        {"_id": 0}
    ))
    return resenas


# ── POST /api/hoteles/{hotel_id}/resenas ────────────────────────
# Inserta una reseña en la colección 'reseñas'
@app.post('/api/hoteles/{hotel_id}/resenas')
def post_resena(hotel_id: str, datos: dict):
    # Validar campos requeridos
    for campo in ["cliente_id", "reserva_id", "calificacion", "comentario"]:
        if campo not in datos:
            raise HTTPException(status_code=400, detail=f"Campo requerido: {campo}")

    # Validar calificación
    cal = datos["calificacion"]
    if not isinstance(cal, (str, float)) or cal < 1 or cal > 5:
        raise HTTPException(status_code=400, detail="La calificación debe estar entre 1 y 5.")

    # Verificar que la reserva exista y esté completada
    reserva = db["Reservas"].find_one({"reserva_id": datos["reserva_id"]})
    if not reserva:
        raise HTTPException(status_code=404, detail="Reserva no encontrada.")
    if reserva.get("estado") != "completada":
        raise HTTPException(status_code=403, detail="Solo se pueden reseñar reservas completadas.")

    # Verificar que no haya reseñado esta reserva antes
    existente = db["reseñas"].find_one({
        "reserva_id": datos["reserva_id"],
        "cliente_id": datos["cliente_id"],
        "estado": "publicada"
    })
    if existente:
        raise HTTPException(status_code=409, detail="Ya existe una reseña para esta reserva.")

    # Construir y guardar la reseña
    resena = {
        "hotel_id":     hotel_id,
        "cliente_id":   datos["cliente_id"],
        "reserva_id":   datos["reserva_id"],
        "calificacion": cal,
        "comentario":   datos["comentario"],
        "fecha":        datetime.now().isoformat(),
        "estado":       "publicada",
        "votos_utiles": 0,
        "respuesta":    None,
    }
    db["reseñas"].insert_one(resena)

    return {"mensaje": "Reseña guardada"}