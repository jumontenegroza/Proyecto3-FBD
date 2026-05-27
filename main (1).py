from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient, DESCENDING, ASCENDING
from datetime import datetime, date, timedelta
from uuid import uuid4
import os, random

# ─────────────────────────────────────────────
#  CONEXIÓN
# ─────────────────────────────────────────────
client = MongoClient(os.environ["MONGO_URI"])
db     = client["ISIS2304D31202610"]

app = FastAPI(title="Dann-Alpes – Reseñas API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────
#  HEALTH CHECK
# ─────────────────────────────────────────────
@app.get("/")
def inicio():
    return {"estado": "API Dann-Alpes funcionando correctamente"}


# ══════════════════════════════════════════════════════════════════
#  RF1 – CREAR RESEÑA
#  POST /resenas
#  Solo si la reserva está "completada" y no ha reseñado esa estadía
# ══════════════════════════════════════════════════════════════════
@app.post("/resenas")
def crear_resena(datos: dict):
    """
    Body requerido:
    {
        "cliente_doc_id": 1000000001,
        "codigo_reserva": "RES1",
        "calificacion": 4,          (1 a 5)
        "texto": "Excelente estadía..."
    }
    """
    for campo in ["cliente_doc_id", "codigo_reserva", "calificacion", "texto"]:
        if campo not in datos:
            raise HTTPException(400, f"Campo requerido: {campo}")

    cal = datos["calificacion"]
    if not isinstance(cal, (int, float)) or cal < 1 or cal > 5:
        raise HTTPException(400, "La calificación debe estar entre 1 y 5.")

    # Verificar que la reserva exista y esté completada
    reserva = db["reservas"].find_one({"codigo_reserva": datos["codigo_reserva"]})
    if not reserva:
        raise HTTPException(404, "Reserva no encontrada.")
    if reserva.get("estado", "completada") != "completada":
        raise HTTPException(403, "Solo se pueden reseñar reservas completadas.")
    if reserva["cliente_doc_id"] != datos["cliente_doc_id"]:
        raise HTTPException(403, "Esta reserva no pertenece al cliente indicado.")

    # Verificar que no haya reseñado esta reserva antes
    existente = db["resenas"].find_one({
        "codigo_reserva": datos["codigo_reserva"],
        "cliente_doc_id": datos["cliente_doc_id"],
        "estado": "publicada"
    })
    if existente:
        raise HTTPException(409, "Ya existe una reseña para esta reserva.")

    # Obtener datos del hotel desde la colección hoteles
    habitacion = db["habitaciones"].find_one({"id_habitacion": reserva["id_habitacion"]})
    hotel = db["hoteles"].find_one({"direccion": habitacion["hotel_direccion"]}) if habitacion else None

    resena = {
        "id_resena":       str(uuid4()),
        "hotel_direccion": hotel["direccion"]    if hotel else "",
        "nombre_hotel":    hotel["nombre_hotel"] if hotel else "",
        "ciudad_nombre":   hotel["ciudad_nombre"] if hotel else "",
        "cliente_doc_id":  datos["cliente_doc_id"],
        "codigo_reserva":  datos["codigo_reserva"],
        "calificacion":    cal,
        "texto":           datos["texto"],
        "fecha_creacion":  datetime.now().isoformat(),
        "fecha_edicion":   None,
        "estado":          "publicada",
        "destacada":       False,
        "votos_utiles":    0,
        "usuarios_voto":   [],
        "respuesta_admin": None,
    }
    db["resenas"].insert_one(resena)
    resena.pop("_id", None)
    return {"mensaje": "Reseña creada exitosamente", "resena": resena}


# ══════════════════════════════════════════════════════════════════
#  RF2 – EDITAR RESEÑA
#  PUT /resenas/{id_resena}
# ══════════════════════════════════════════════════════════════════
@app.put("/resenas/{id_resena}")
def editar_resena(id_resena: str, datos: dict):
    """
    Body (al menos uno requerido):
    {
        "cliente_doc_id": 1000000001,
        "calificacion": 5,
        "texto": "Nuevo texto..."
    }
    """
    resena = db["resenas"].find_one({"id_resena": id_resena, "estado": "publicada"})
    if not resena:
        raise HTTPException(404, "Reseña no encontrada o ya eliminada.")
    if resena["cliente_doc_id"] != datos.get("cliente_doc_id"):
        raise HTTPException(403, "No tiene permiso para editar esta reseña.")

    cambios = {"fecha_edicion": datetime.now().isoformat()}
    if "calificacion" in datos:
        cal = datos["calificacion"]
        if not isinstance(cal, (int, float)) or cal < 1 or cal > 5:
            raise HTTPException(400, "La calificación debe estar entre 1 y 5.")
        cambios["calificacion"] = cal
    if "texto" in datos:
        cambios["texto"] = datos["texto"]

    db["resenas"].update_one({"id_resena": id_resena}, {"$set": cambios})
    return {"mensaje": "Reseña actualizada exitosamente", "id_resena": id_resena}


# ══════════════════════════════════════════════════════════════════
#  RF3 – ELIMINAR RESEÑA (cliente)
#  DELETE /resenas/{id_resena}
# ══════════════════════════════════════════════════════════════════
@app.delete("/resenas/{id_resena}")
def eliminar_resena_cliente(id_resena: str, cliente_doc_id: int = Query(...)):
    resena = db["resenas"].find_one({"id_resena": id_resena})
    if not resena:
        raise HTTPException(404, "Reseña no encontrada.")
    if resena["cliente_doc_id"] != cliente_doc_id:
        raise HTTPException(403, "No tiene permiso para eliminar esta reseña.")

    db["resenas"].update_one(
        {"id_resena": id_resena},
        {"$set": {"estado": "eliminada"}}
    )
    return {"mensaje": "Reseña eliminada exitosamente", "id_resena": id_resena}


# ══════════════════════════════════════════════════════════════════
#  RF4 – CONSULTAR RESEÑAS DE UN HOTEL (público, paginado)
#  GET /hoteles/{hotel_direccion}/resenas
# ══════════════════════════════════════════════════════════════════
@app.get("/hoteles/{hotel_direccion}/resenas")
def get_resenas_hotel(
    hotel_direccion: str,
    orden: str = Query("fecha", enum=["fecha", "utilidad"]),
    pagina: int = Query(1, ge=1),
    por_pagina: int = Query(10, ge=1, le=50),
):
    """
    orden: 'fecha' (más reciente) | 'utilidad' (más votos)
    """
    campo_orden = "fecha_creacion" if orden == "fecha" else "votos_utiles"
    filtro = {"hotel_direccion": hotel_direccion, "estado": "publicada"}

    # Destacada primero, luego el orden elegido
    pipeline = [
        {"$match": filtro},
        {"$sort": {"destacada": DESCENDING, campo_orden: DESCENDING}},
        {"$skip": (pagina - 1) * por_pagina},
        {"$limit": por_pagina},
        {"$project": {
            "_id": 0,
            "usuarios_voto": 0   # no exponer quiénes votaron
        }}
    ]
    total   = db["resenas"].count_documents(filtro)
    resenas = list(db["resenas"].aggregate(pipeline))
    return {
        "hotel_direccion": hotel_direccion,
        "total":           total,
        "pagina":          pagina,
        "por_pagina":      por_pagina,
        "resenas":         resenas,
    }


# ══════════════════════════════════════════════════════════════════
#  RF5 – MARCAR RESEÑA COMO ÚTIL
#  POST /resenas/{id_resena}/votos
# ══════════════════════════════════════════════════════════════════
@app.post("/resenas/{id_resena}/votos")
def votar_resena(id_resena: str, datos: dict):
    """
    Body: { "cliente_doc_id": 1000000001 }
    """
    cliente_doc_id = datos.get("cliente_doc_id")
    if not cliente_doc_id:
        raise HTTPException(400, "El campo 'cliente_doc_id' es requerido.")

    resena = db["resenas"].find_one({"id_resena": id_resena, "estado": "publicada"})
    if not resena:
        raise HTTPException(404, "Reseña no encontrada.")
    if resena["cliente_doc_id"] == cliente_doc_id:
        raise HTTPException(403, "No puede votar su propia reseña.")
    if cliente_doc_id in resena.get("usuarios_voto", []):
        raise HTTPException(409, "Ya votó por esta reseña.")

    db["resenas"].update_one(
        {"id_resena": id_resena},
        {
            "$inc":  {"votos_utiles": 1},
            "$push": {"usuarios_voto": cliente_doc_id}
        }
    )
    return {"mensaje": "Voto registrado", "id_resena": id_resena}


# ══════════════════════════════════════════════════════════════════
#  RF6 – HISTORIAL DE RESEÑAS PROPIAS
#  GET /clientes/{doc_identidad}/resenas
# ══════════════════════════════════════════════════════════════════
@app.get("/clientes/{doc_identidad}/resenas")
def get_resenas_cliente(
    doc_identidad: int,
    orden: str = Query("fecha", enum=["fecha", "hotel"]),
):
    campo_orden = "fecha_creacion" if orden == "fecha" else "nombre_hotel"
    pipeline = [
        {"$match": {"cliente_doc_id": doc_identidad}},
        {"$sort": {campo_orden: DESCENDING if orden == "fecha" else ASCENDING}},
        {"$project": {
            "_id": 0,
            "id_resena":        1,
            "nombre_hotel":     1,
            "hotel_direccion":  1,
            "codigo_reserva":   1,
            "calificacion":     1,
            "texto":            1,
            "fecha_creacion":   1,
            "estado":           1,
            "votos_utiles":     1,
            "tiene_respuesta":  {"$cond": [{"$ne": ["$respuesta_admin", None]}, True, False]}
        }}
    ]
    resenas = list(db["resenas"].aggregate(pipeline))
    return {"cliente_doc_id": doc_identidad, "total": len(resenas), "resenas": resenas}


# ══════════════════════════════════════════════════════════════════
#  RF7 – RESPONDER RESEÑA (admin)
#  PUT /admin/resenas/{id_resena}/respuesta
# ══════════════════════════════════════════════════════════════════
@app.put("/admin/resenas/{id_resena}/respuesta")
def responder_resena(id_resena: str, datos: dict):
    """
    Body: { "texto_respuesta": "Gracias por su visita..." }
    """
    if not datos.get("texto_respuesta"):
        raise HTTPException(400, "El campo 'texto_respuesta' es requerido.")

    resena = db["resenas"].find_one({"id_resena": id_resena, "estado": "publicada"})
    if not resena:
        raise HTTPException(404, "Reseña no encontrada.")

    respuesta = {
        "texto": datos["texto_respuesta"],
        "fecha": datetime.now().isoformat()
    }
    db["resenas"].update_one(
        {"id_resena": id_resena},
        {"$set": {"respuesta_admin": respuesta}}
    )
    return {"mensaje": "Respuesta registrada exitosamente", "id_resena": id_resena, "respuesta": respuesta}


# ══════════════════════════════════════════════════════════════════
#  RF8 – ELIMINAR RESEÑA (admin)
#  DELETE /admin/resenas/{id_resena}
# ══════════════════════════════════════════════════════════════════
@app.delete("/admin/resenas/{id_resena}")
def eliminar_resena_admin(id_resena: str):
    resena = db["resenas"].find_one({"id_resena": id_resena})
    if not resena:
        raise HTTPException(404, "Reseña no encontrada.")

    db["resenas"].update_one(
        {"id_resena": id_resena},
        {"$set": {"estado": "eliminada", "destacada": False}}
    )
    return {"mensaje": "Reseña eliminada por administrador", "id_resena": id_resena}


# ══════════════════════════════════════════════════════════════════
#  RF9 – DESTACAR RESEÑA (admin)
#  PUT /admin/resenas/{id_resena}/destacar
#  Solo una reseña destacada por hotel a la vez
# ══════════════════════════════════════════════════════════════════
@app.put("/admin/resenas/{id_resena}/destacar")
def destacar_resena(id_resena: str):
    resena = db["resenas"].find_one({"id_resena": id_resena, "estado": "publicada"})
    if not resena:
        raise HTTPException(404, "Reseña no encontrada.")

    # Quitar destacada anterior del mismo hotel
    db["resenas"].update_many(
        {"hotel_direccion": resena["hotel_direccion"], "destacada": True},
        {"$set": {"destacada": False}}
    )
    # Destacar la nueva
    db["resenas"].update_one(
        {"id_resena": id_resena},
        {"$set": {"destacada": True}}
    )
    return {"mensaje": "Reseña destacada exitosamente", "id_resena": id_resena}


# ══════════════════════════════════════════════════════════════════
#  RFC1 – TOP 10 HOTELES POR CALIFICACIÓN EN UN PERÍODO
#  GET /consultas/top-hoteles?fecha_inicio=YYYY-MM-DD&fecha_fin=YYYY-MM-DD
# ══════════════════════════════════════════════════════════════════
@app.get("/consultas/top-hoteles")
def top_hoteles(
    fecha_inicio: str = Query(..., description="YYYY-MM-DD"),
    fecha_fin:    str = Query(..., description="YYYY-MM-DD"),
):
    pipeline = [
        {"$match": {
            "estado": "publicada",
            "fecha_creacion": {"$gte": fecha_inicio, "$lte": fecha_fin + "T23:59:59"}
        }},
        {"$group": {
            "_id":              "$hotel_direccion",
            "nombre_hotel":     {"$first": "$nombre_hotel"},
            "ciudad":           {"$first": "$ciudad_nombre"},
            "calificacion_prom":{"$avg": "$calificacion"},
            "total_resenas":    {"$sum": 1}
        }},
        {"$sort": {"calificacion_prom": DESCENDING}},
        {"$limit": 10},
        {"$project": {
            "_id": 0,
            "hotel_direccion":  "$_id",
            "nombre_hotel":     1,
            "ciudad":           1,
            "calificacion_prom":{"$round": ["$calificacion_prom", 2]},
            "total_resenas":    1
        }}
    ]
    resultado = list(db["resenas"].aggregate(pipeline))
    return {"periodo": {"inicio": fecha_inicio, "fin": fecha_fin}, "top_hoteles": resultado}


# ══════════════════════════════════════════════════════════════════
#  RFC2 – EVOLUCIÓN DE REPUTACIÓN DE UN HOTEL MES A MES
#  GET /consultas/evolucion/{hotel_direccion}?anio=2026
# ══════════════════════════════════════════════════════════════════
@app.get("/consultas/evolucion/{hotel_direccion}")
def evolucion_reputacion(hotel_direccion: str, anio: int = Query(...)):
    pipeline = [
        {"$match": {
            "hotel_direccion": hotel_direccion,
            "estado":          "publicada",
            "fecha_creacion":  {
                "$gte": f"{anio}-01-01",
                "$lte": f"{anio}-12-31T23:59:59"
            }
        }},
        {"$addFields": {
            "mes": {"$substr": ["$fecha_creacion", 5, 2]}   # extrae MM de ISO string
        }},
        {"$group": {
            "_id":              "$mes",
            "calificacion_prom":{"$avg": "$calificacion"},
            "total_resenas":    {"$sum": 1}
        }},
        {"$sort": {"_id": ASCENDING}},
        {"$project": {
            "_id": 0,
            "mes":              "$_id",
            "calificacion_prom":{"$round": ["$calificacion_prom", 2]},
            "total_resenas":    1
        }}
    ]
    hotel  = db["hoteles"].find_one({"direccion": hotel_direccion}, {"_id": 0})
    evolucion = list(db["resenas"].aggregate(pipeline))
    return {
        "hotel":     hotel,
        "anio":      anio,
        "evolucion": evolucion
    }


# ══════════════════════════════════════════════════════════════════
#  RFC3 – PERFIL COMPARATIVO DE HOTELES POR CIUDAD
#  GET /consultas/comparativo/{ciudad}
# ══════════════════════════════════════════════════════════════════
@app.get("/consultas/comparativo/{ciudad}")
def comparativo_ciudad(ciudad: str):
    pipeline = [
        {"$match": {"ciudad_nombre": ciudad, "estado": "publicada"}},
        {"$group": {
            "_id":              "$hotel_direccion",
            "nombre_hotel":     {"$first": "$nombre_hotel"},
            "calificacion_prom":{"$avg": "$calificacion"},
            "total_resenas":    {"$sum": 1},
            "con_respuesta":    {"$sum": {"$cond": [{"$ne": ["$respuesta_admin", None]}, 1, 0]}},
            "destacadas":       {"$sum": {"$cond": ["$destacada", 1, 0]}},
        }},
        {"$project": {
            "_id": 0,
            "hotel_direccion":   "$_id",
            "nombre_hotel":      1,
            "calificacion_prom": {"$round": ["$calificacion_prom", 2]},
            "total_resenas":     1,
            "pct_con_respuesta": {"$round": [
                {"$multiply": [{"$divide": ["$con_respuesta", "$total_resenas"]}, 100]}, 1
            ]},
            "pct_destacadas": {"$round": [
                {"$multiply": [{"$divide": ["$destacadas", "$total_resenas"]}, 100]}, 1
            ]},
        }},
        {"$sort": {"calificacion_prom": DESCENDING}}
    ]
    hoteles_ciudad = list(db["resenas"].aggregate(pipeline))

    # Promedio de la ciudad para identificar hoteles por debajo
    prom_ciudad = (
        sum(h["calificacion_prom"] for h in hoteles_ciudad) / len(hoteles_ciudad)
        if hoteles_ciudad else 0
    )
    for h in hoteles_ciudad:
        h["bajo_promedio_ciudad"] = h["calificacion_prom"] < round(prom_ciudad, 2)

    return {
        "ciudad":            ciudad,
        "promedio_ciudad":   round(prom_ciudad, 2),
        "total_hoteles":     len(hoteles_ciudad),
        "hoteles":           hoteles_ciudad
    }


# ══════════════════════════════════════════════════════════════════
#  POBLAR – Llama UNA SOLA VEZ después de desplegar
#  GET /poblar
# ══════════════════════════════════════════════════════════════════
@app.get("/poblar")
def poblar():
    resultados = {}

    # ── CIUDADES ──────────────────────────────
    db["ciudades"].drop()
    db["ciudades"].insert_many([
        {"nombre": "Bogota"}, {"nombre": "Medellin"}, {"nombre": "Cartagena"},
        {"nombre": "Cali"},   {"nombre": "Barranquilla"},
    ])
    resultados["ciudades"] = db["ciudades"].count_documents({})

    # ── CAMAS ─────────────────────────────────
    db["camas"].drop()
    db["camas"].insert_many([
        {"id_tipo_cama": 1, "tipo_nombre": "Sencilla"},
        {"id_tipo_cama": 2, "tipo_nombre": "Doble"},
        {"id_tipo_cama": 3, "tipo_nombre": "Queen"},
        {"id_tipo_cama": 4, "tipo_nombre": "King"},
    ])
    resultados["camas"] = db["camas"].count_documents({})

    # ── COMODIDADES ───────────────────────────
    db["comodidades"].drop()
    db["comodidades"].insert_many([
        {"nombre": "WiFi"}, {"nombre": "TV"}, {"nombre": "Aire acondicionado"},
        {"nombre": "Minibar"}, {"nombre": "Balcon"},
    ])
    resultados["comodidades"] = db["comodidades"].count_documents({})

    # ── SERVICIOS ─────────────────────────────
    db["servicios"].drop()
    db["servicios"].insert_many([
        {"nombre": "Desayuno",          "descripcion": "Por persona",        "precio": 25000, "tipo": "CONSUMO1"},
        {"nombre": "Spa",               "descripcion": "Sesion",             "precio": 90000, "tipo": "CONSUMO2"},
        {"nombre": "Check-in temprano", "descripcion": "Ingreso anticipado", "precio": 40000, "tipo": "FIJO1"},
        {"nombre": "Check-out tardio",  "descripcion": "Salida extendida",   "precio": 40000, "tipo": "FIJO2"},
    ])
    resultados["servicios"] = db["servicios"].count_documents({})

    # ── TIPOS DE HABITACIÓN ───────────────────
    camas_base       = [{"id_tipo_cama": 1, "tipo_nombre": "Sencilla", "cantidad": 1},
                        {"id_tipo_cama": 2, "tipo_nombre": "Doble",    "cantidad": 1}]
    comodidades_base = ["WiFi", "TV"]
    tipos = [{"id_tipo": i, "nombre": f"Tipo {i}", "costo_alta": 200000 + i*50000,
              "costo_baja": 150000 + i*40000, "dimensiones": 20 + i*5,
              "vista": f"Vista {i}", "capacidad": 2+i,
              "camas": camas_base, "comodidades": comodidades_base}
             for i in range(1, 6)]
    db["tipos_habitacion"].drop()
    db["tipos_habitacion"].insert_many(tipos)
    resultados["tipos_habitacion"] = db["tipos_habitacion"].count_documents({})

    # ── HOTELES ───────────────────────────────
    ciudades_map = {0:"Bogota",1:"Medellin",2:"Cartagena",3:"Cali",4:"Barranquilla"}
    hoteles = [{"nombre_hotel": f"Dann-Alpes Hotel {i}",
                "direccion": f"Calle {10+i} # {20+i}-{30+i}",
                "telefono": 601000000+i, "descripcion": f"Hotel de prueba {i}",
                "ciudad_nombre": ciudades_map[i % 5]}
               for i in range(1, 16)]
    db["hoteles"].drop()
    db["hoteles"].insert_many(hoteles)
    resultados["hoteles"] = db["hoteles"].count_documents({})

    # ── HABITACIONES ──────────────────────────
    habitaciones = []
    id_hab = 1
    for h in range(1, 16):
        for j in range(1, 51):
            habitaciones.append({"id_habitacion": id_hab, "numero": j,
                                  "hotel_direccion": f"Calle {10+h} # {20+h}-{30+h}",
                                  "id_tipo": (j % 5) + 1})
            id_hab += 1
    db["habitaciones"].drop()
    db["habitaciones"].insert_many(habitaciones)
    resultados["habitaciones"] = db["habitaciones"].count_documents({})

    # ── CLIENTES ──────────────────────────────
    clientes = [{"doc_identidad": 1000000000+i, "nombre_clientee": f"Nombre{i}",
                 "apellidos_cliente": f"Apellido{i}", "correo_cliente": f"cliente{i}@correo.com",
                 "telefono_cliente": f"300{str(i).zfill(7)}"}
                for i in range(1, 301)]
    db["clientes"].drop()
    db["clientes"].insert_many(clientes)
    resultados["clientes"] = db["clientes"].count_documents({})

    # ── RESERVAS (todas con estado completada para poder reseñar) ──
    random.seed(42)
    reservas = []
    id_res = 1
    base   = date(2026, 1, 1)
    for h in range(1, 16):
        for j in range(1, 21):
            noches = random.randint(1, 6)
            inicio = base + timedelta(days=random.randint(0, 329))
            reservas.append({
                "id_reserva": id_res, "codigo_reserva": f"RES{id_res}",
                "fecha_inicio": inicio.isoformat(),
                "fecha_final": (inicio + timedelta(days=noches)).isoformat(),
                "numero_noches": noches, "cant_mayores": random.randint(1,3),
                "cant_menores": random.randint(0,1),
                "costo_total": 200000 + noches*150000,
                "cliente_doc_id": 1000000000 + (h-1)*20 + j,
                "id_habitacion": (h-1)*50 + j,
                "estado": "completada",    # <── necesario para RF1
                "servicios": []
            })
            id_res += 1
    db["reservas"].drop()
    db["reservas"].insert_many(reservas)
    resultados["reservas"] = db["reservas"].count_documents({})

    # ── RESEÑAS DE PRUEBA (para que RFC1, RFC2, RFC3 tengan datos) ─
    textos = [
        "Excelente servicio y ubicación inmejorable.",
        "Habitaciones cómodas, el desayuno fue delicioso.",
        "Buen hotel, pero el WiFi es lento.",
        "Personal muy amable, lo recomiendo totalmente.",
        "La vista desde la habitación es espectacular.",
        "Instalaciones modernas y limpias.",
        "Precio justo por la calidad ofrecida.",
        "Ambiente tranquilo, perfecto para descansar.",
        "El check-in tardó demasiado.",
        "Regresaré sin duda en mi próximo viaje.",
    ]
    respuestas_admin = [
        "Gracias por su visita, esperamos verle pronto.",
        "Agradecemos sus comentarios, seguimos mejorando.",
        "Trabajamos para mejorar la conectividad.",
        None, None,
    ]
    random.seed(7)
    resenas = []
    reservas_docs = list(db["reservas"].find({}, {"_id": 0}))
    hoteles_docs  = {h["direccion"]: h for h in db["hoteles"].find({}, {"_id": 0})}
    habs_docs     = {h["id_habitacion"]: h for h in db["habitaciones"].find({}, {"_id": 0})}

    for res in reservas_docs:
        hab   = habs_docs.get(res["id_habitacion"], {})
        hotel = hoteles_docs.get(hab.get("hotel_direccion", ""), {})
        mes   = random.randint(1, 12)
        dia   = random.randint(1, 28)
        fecha = f"2026-{mes:02d}-{dia:02d}T{random.randint(8,22):02d}:00:00"
        resp  = respuestas_admin[random.randint(0, 4)]

        resena = {
            "id_resena":       str(uuid4()),
            "hotel_direccion": hotel.get("direccion", ""),
            "nombre_hotel":    hotel.get("nombre_hotel", ""),
            "ciudad_nombre":   hotel.get("ciudad_nombre", ""),
            "cliente_doc_id":  res["cliente_doc_id"],
            "codigo_reserva":  res["codigo_reserva"],
            "calificacion":    random.randint(1, 5),
            "texto":           random.choice(textos),
            "fecha_creacion":  fecha,
            "fecha_edicion":   None,
            "estado":          "publicada",
            "destacada":       False,
            "votos_utiles":    random.randint(0, 50),
            "usuarios_voto":   [],
            "respuesta_admin": {"texto": resp, "fecha": fecha} if resp else None,
        }
        resenas.append(resena)

    db["resenas"].drop()
    db["resenas"].insert_many(resenas)
    resultados["resenas"] = db["resenas"].count_documents({})

    return {"mensaje": "✅ Base de datos poblada exitosamente", "conteos": resultados}