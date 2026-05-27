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

# os.environ para despliegue. Descomente cuando ya probó todo local.
client = MongoClient(os.environ["MONGO_URI"])

# TODO: conectarse a la base de datos del grupo
# db = client["ISIS2304D31202610"]
db = client["ISIS2304D31202610"]


@app.get("/")
def inicio():
    return {"estado": "API Dann-Alpes funcionando correctamente"}


# ── GET /api/hoteles/{hotel_direccion}/resenas ──────────────────
# Retorna todas las reseñas publicadas del hotel desde la colección 'resenas'
@app.get('/api/hoteles/{hotel_direccion}/resenas')
def get_resenas(hotel_direccion: str):
    resenas = list(db.resenas.find(
        {"hotel_direccion": hotel_direccion, "estado": "publicada"},
        {"_id": 0}
    ))
    return resenas


# ── POST /api/hoteles/{hotel_direccion}/resenas ─────────────────
# Inserta una reseña en la colección 'resenas'
# Verifica que la reserva esté completada y que no exista reseña previa
@app.post('/api/hoteles/{hotel_direccion}/resenas')
def post_resena(hotel_direccion: str, datos: dict):
    # Validar campos requeridos
    for campo in ["cliente_doc_id", "codigo_reserva", "calificacion", "texto"]:
        if campo not in datos:
            raise HTTPException(status_code=400, detail=f"Campo requerido: {campo}")

    # Validar calificación
    cal = datos["calificacion"]
    if not isinstance(cal, (int, float)) or cal < 1 or cal > 5:
        raise HTTPException(status_code=400, detail="La calificación debe estar entre 1 y 5.")

    # Verificar que la reserva exista y esté completada
    reserva = db.reservas.find_one({"codigo_reserva": datos["codigo_reserva"]})
    if not reserva:
        raise HTTPException(status_code=404, detail="Reserva no encontrada.")
    if reserva.get("estado") != "completada":
        raise HTTPException(status_code=403, detail="Solo se pueden reseñar reservas completadas.")
    if reserva["cliente_doc_id"] != datos["cliente_doc_id"]:
        raise HTTPException(status_code=403, detail="Esta reserva no pertenece al cliente indicado.")

    # Verificar que no haya reseñado esta reserva antes
    existente = db.resenas.find_one({
        "codigo_reserva": datos["codigo_reserva"],
        "cliente_doc_id": datos["cliente_doc_id"],
        "estado": "publicada"
    })
    if existente:
        raise HTTPException(status_code=409, detail="Ya existe una reseña para esta reserva.")

    # Construir y guardar la reseña
    resena = {
        "id_resena":       str(uuid4()),
        "hotel_direccion": hotel_direccion,
        "cliente_doc_id":  datos["cliente_doc_id"],
        "codigo_reserva":  datos["codigo_reserva"],
        "calificacion":    cal,
        "texto":           datos["texto"],
        "fecha_creacion":  datetime.now().isoformat(),
        "estado":          "publicada",
        "votos_utiles":    0,
        "respuesta_admin": None,
    }
    db.resenas.insert_one(resena)

    return {"mensaje": "Reseña guardada"}