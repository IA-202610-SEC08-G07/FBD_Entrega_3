from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError
from datetime import datetime
from bson import ObjectId
import os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

client = MongoClient(os.environ["MONGO_URI"])
db = client["ISIS2304I16202610"]

# ── helper: convierte ObjectId y datetime a string para poder retornar el doc ──
def ser(doc):
    if doc is None:
        return None
    doc["_id"] = str(doc["_id"])
    if isinstance(doc.get("respuesta_admin"), dict):
        if isinstance(doc["respuesta_admin"].get("fecha"), datetime):
            doc["respuesta_admin"]["fecha"] = doc["respuesta_admin"]["fecha"].isoformat()
    for campo in ["fecha_creacion", "fecha_edicion"]:
        if isinstance(doc.get(campo), datetime):
            doc[campo] = doc[campo].isoformat()
    return doc


@app.get("/")
def inicio():
    return {"estado": "API Dann-Alpes funcionando correctamente"}

# RF1 – Crear reseña
# POST /resenas
# Body: { hotel_id (str), ciudad_hotel, cliente_id (str),
#         nombre_cliente, reserva_id (str), calificacion, texto }
@app.post("/resenas")
def crear_resena(datos: dict):
    if not (1 <= int(datos.get("calificacion", 0)) <= 5):
        raise HTTPException(400, "La calificación debe estar entre 1 y 5.")
    if len(datos.get("texto", "")) < 10:
        raise HTTPException(400, "El texto debe tener al menos 10 caracteres.")

    # Asegurar que los IDs lleguen como string
    datos["hotel_id"]   = str(datos["hotel_id"])
    datos["cliente_id"] = str(datos["cliente_id"])
    datos["reserva_id"] = str(datos["reserva_id"])

    datos["calificacion"]       = int(datos["calificacion"])
    datos["fecha_creacion"]     = datetime.utcnow()
    datos["fecha_edicion"]      = None
    datos["estado"]             = "publicada"
    datos["destacada"]          = False
    datos["votos_utiles_count"] = 0
    datos["respuesta_admin"]    = None

    try:
        result = db["resenas"].insert_one(datos)
        return {"mensaje": "Reseña creada", "id": str(result.inserted_id)}
    except DuplicateKeyError:
        raise HTTPException(409, "Esta reserva ya tiene una reseña.")


# RF2 – Editar reseña (solo el cliente dueño)
# PUT /resenas/{resena_id}
# Body: { cliente_id (str), calificacion, texto }

@app.put("/resenas/{resena_id}")
def editar_resena(resena_id: str, datos: dict):
    if not (1 <= int(datos.get("calificacion", 0)) <= 5):
        raise HTTPException(400, "La calificación debe estar entre 1 y 5.")
    if len(datos.get("texto", "")) < 10:
        raise HTTPException(400, "El texto debe tener al menos 10 caracteres.")

    result = db["resenas"].find_one_and_update(
        {
            "_id":        ObjectId(resena_id),
            "cliente_id": str(datos["cliente_id"]),
            "estado":     "publicada"
        },
        {"$set": {
            "calificacion":  int(datos["calificacion"]),
            "texto":         datos["texto"],
            "fecha_edicion": datetime.utcnow()
        }},
        return_document=True
    )
    if not result:
        raise HTTPException(404, "Reseña no encontrada o no autorizado.")
    return {"mensaje": "Reseña actualizada", "resena": ser(result)}

# RF3 – Eliminar reseña (cliente)
# DELETE /resenas/{resena_id}/cliente?cliente_id=CLI000123

@app.delete("/resenas/{resena_id}/cliente")
def eliminar_resena_cliente(resena_id: str, cliente_id: str):
    result = db["resenas"].find_one_and_update(
        {
            "_id":        ObjectId(resena_id),
            "cliente_id": cliente_id,
            "estado":     "publicada"
        },
        {"$set": {"estado": "eliminada_cliente"}}
    )
    if not result:
        raise HTTPException(404, "Reseña no encontrada.")
    return {"mensaje": "Reseña eliminada"}

# RF4 – Consultar reseñas de un hotel (público, paginado)
# GET /hoteles/{hotel_id}/resenas?orden=fecha&pagina=1&por_pagina=10
@app.get("/hoteles/{hotel_id}/resenas")
def get_resenas_hotel(
    hotel_id:   str,
    orden:      str = Query("fecha"),
    pagina:     int = Query(1),
    por_pagina: int = Query(10)
):
    filtro = {"hotel_id": hotel_id, "estado": "publicada"}

    sort = [("destacada", DESCENDING)]
    if orden == "votos":
        sort += [("votos_utiles_count", DESCENDING), ("fecha_creacion", DESCENDING)]
    else:
        sort += [("fecha_creacion", DESCENDING)]

    total = db["resenas"].count_documents(filtro)
    docs  = list(
        db["resenas"]
        .find(filtro, {"calificacion": 1, "texto": 1, "fecha_creacion": 1,
                       "votos_utiles_count": 1, "nombre_cliente": 1,
                       "destacada": 1, "respuesta_admin": 1})
        .sort(sort)
        .skip((pagina - 1) * por_pagina)
        .limit(por_pagina)
    )
    return {"total": total, "pagina": pagina, "resenas": [ser(d) for d in docs]}


# RF5 – Marcar reseña como útil
# POST /resenas/{resena_id}/voto
# Body: { cliente_id (str) }

@app.post("/resenas/{resena_id}/voto")
def votar_resena(resena_id: str, datos: dict):
    try:
        db["votos_utilidad"].insert_one({
            "resena_id":  ObjectId(resena_id),
            "cliente_id": str(datos["cliente_id"]),
            "fecha_voto": datetime.utcnow()
        })
    except DuplicateKeyError:
        raise HTTPException(409, "Ya votaste por esta reseña.")

    count = db["votos_utilidad"].count_documents({"resena_id": ObjectId(resena_id)})
    db["resenas"].update_one({"_id": ObjectId(resena_id)}, {"$set": {"votos_utiles_count": count}})
    return {"mensaje": "Voto registrado", "votos_utiles_count": count}

# RF6 – Historial de reseñas del cliente autenticado
# GET /clientes/{cliente_id}/resenas?orden=fecha

@app.get("/clientes/{cliente_id}/resenas")
def get_historial_cliente(
    cliente_id: str,
    orden: str = Query("fecha")
):
    sort = [("hotel_id", ASCENDING)] if orden == "hotel" else [("fecha_creacion", DESCENDING)]
    docs = list(
        db["resenas"]
        .find({"cliente_id": cliente_id},
              {"hotel_id": 1, "calificacion": 1, "estado": 1,
               "fecha_creacion": 1, "votos_utiles_count": 1, "respuesta_admin": 1})
        .sort(sort)
    )
    return {"resenas": [ser(d) for d in docs]}


# RF7 – Responder reseña (admin)
# PUT /resenas/{resena_id}/respuesta
# Body: { admin_id (str), nombre_admin, texto }

@app.put("/resenas/{resena_id}/respuesta")
def responder_resena(resena_id: str, datos: dict):
    if len(datos.get("texto", "")) < 5:
        raise HTTPException(400, "La respuesta debe tener al menos 5 caracteres.")

    respuesta = {
        "admin_id":    str(datos["admin_id"]),
        "nombre_admin": datos["nombre_admin"],
        "texto":        datos["texto"],
        "fecha":        datetime.utcnow()
    }
    result = db["resenas"].find_one_and_update(
        {"_id": ObjectId(resena_id)},
        {"$set": {"respuesta_admin": respuesta}},
        return_document=True
    )
    if not result:
        raise HTTPException(404, "Reseña no encontrada.")
    return {"mensaje": "Respuesta guardada"}

# RF8 – Eliminar reseña (admin)
# DELETE /resenas/{resena_id}/admin

@app.delete("/resenas/{resena_id}/admin")
def eliminar_resena_admin(resena_id: str):
    result = db["resenas"].find_one_and_update(
        {"_id": ObjectId(resena_id)},
        {"$set": {"estado": "eliminada_admin"}}
    )
    if not result:
        raise HTTPException(404, "Reseña no encontrada.")
    return {"mensaje": "Reseña eliminada por administrador"}


# RF9 – Destacar reseña (admin — solo 1 por hotel a la vez)
# PUT /resenas/{resena_id}/destacar
# Body: { hotel_id (str) }

@app.put("/resenas/{resena_id}/destacar")
def destacar_resena(resena_id: str, datos: dict):
    # 1. Buscar la reseña actual para saber su estado e identificar su hotel
    resena_actual = db["resenas"].find_one({"_id": ObjectId(resena_id)})
    if not resena_actual:
        raise HTTPException(status_code=404, detail="Reseña no encontrada.")
    nuevo_estado = not resena_actual.get("destacada", False)
    hotel_id = str(datos["hotel_id"])

    if nuevo_estado:
        db["resenas"].update_many(
            {"hotel_id": hotel_id, "destacada": True},
            {"$set": {"destacada": False}}
        )
        db["resenas"].update_one(
            {"_id": ObjectId(resena_id)},
            {"$set": {"destacada": True}}
        )
        mensaje = "Reseña marcada como destacada y las demás fueron desactivadas."
    else:
        db["resenas"].update_one(
            {"_id": ObjectId(resena_id)},
            {"$set": {"destacada": False}}
        )
        mensaje = "Se ha quitado el destacado de la reseña."

    return {"mensaje": mensaje, "destacada": nuevo_estado}


# RFC1 – Top 10 hoteles por calificación promedio en un período
# GET /analytics/top-hoteles?fecha_inicio=2025-01-01&fecha_fin=2025-12-31

@app.get("/analytics/top-hoteles")
def rfc1_top_hoteles(fecha_inicio: str, fecha_fin: str):
    pipeline = [
        {"$match": {
            "estado": "publicada",
            "fecha_creacion": {
                "$gte": datetime.fromisoformat(fecha_inicio),
                "$lte": datetime.fromisoformat(fecha_fin)
            }
        }},
        {"$group": {
            "_id":                   "$hotel_id",
            "calificacion_promedio": {"$avg": "$calificacion"},
            "total_resenas":         {"$sum": 1},
            "total_votos_utiles":    {"$sum": "$votos_utiles_count"}
        }},
        {"$addFields": {"calificacion_promedio": {"$round": ["$calificacion_promedio", 2]}}},
        {"$sort": {"calificacion_promedio": -1, "total_resenas": -1}},
        {"$limit": 10},
        {"$project": {
            "_id": 0, "hotel_id": "$_id",
            "calificacion_promedio": 1, "total_resenas": 1, "total_votos_utiles": 1
        }}
    ]
    return {"resultado": list(db["resenas"].aggregate(pipeline))}


# ================================================================
# RFC2 – Evolución de reputación de un hotel mes a mes
# GET /analytics/evolucion/{hotel_id}?anio=2025
# ================================================================
@app.get("/analytics/evolucion/{hotel_id}")
def rfc2_evolucion(hotel_id: str, anio: int = Query(2025)):
    pipeline = [
        {"$match": {
            "hotel_id": hotel_id,
            "estado":   "publicada",
            "fecha_creacion": {
                "$gte": datetime(anio, 1, 1),
                "$lte": datetime(anio, 12, 31, 23, 59, 59)
            }
        }},
        {"$group": {
            "_id":                   {"mes": {"$dateToString": {"format": "%Y-%m", "date": "$fecha_creacion"}}},
            "calificacion_promedio": {"$avg": "$calificacion"},
            "total_resenas":         {"$sum": 1},
            "total_con_respuesta":   {"$sum": {"$cond": [{"$ne": ["$respuesta_admin", None]}, 1, 0]}}
        }},
        {"$addFields": {
            "calificacion_promedio":    {"$round": ["$calificacion_promedio", 2]},
            "porcentaje_con_respuesta": {"$round": [{"$multiply": [{"$divide": ["$total_con_respuesta", "$total_resenas"]}, 100]}, 1]}
        }},
        {"$sort": {"_id.mes": 1}},
        {"$project": {
            "_id": 0, "mes": "$_id.mes",
            "calificacion_promedio": 1, "total_resenas": 1, "porcentaje_con_respuesta": 1
        }}
    ]
    return {"hotel_id": hotel_id, "anio": anio, "resultado": list(db["resenas"].aggregate(pipeline))}


# RFC3 – Perfil comparativo de hoteles por ciudad
# GET /analytics/ciudad/{ciudad}

@app.get("/analytics/ciudad/{ciudad}")
def rfc3_ciudad(ciudad: str):
    pipeline = [
        {"$match": {"ciudad_hotel": ciudad, "estado": "publicada"}},
        {"$group": {
            "_id":                   "$hotel_id",
            "calificacion_promedio": {"$avg": "$calificacion"},
            "total_resenas":         {"$sum": 1},
            "total_con_respuesta":   {"$sum": {"$cond": [{"$ne": ["$respuesta_admin", None]}, 1, 0]}},
            "total_destacadas":      {"$sum": {"$cond": ["$destacada", 1, 0]}}
        }},
        {"$addFields": {
            "calificacion_promedio":    {"$round": ["$calificacion_promedio", 2]},
            "porcentaje_con_respuesta": {"$round": [{"$multiply": [{"$divide": ["$total_con_respuesta", "$total_resenas"]}, 100]}, 1]},
            "porcentaje_destacadas":    {"$round": [{"$multiply": [{"$divide": ["$total_destacadas",    "$total_resenas"]}, 100]}, 1]}
        }},
        {"$facet": {
            "hoteles": [
                {"$project": {"_id": 0, "hotel_id": "$_id", "calificacion_promedio": 1,
                              "total_resenas": 1, "porcentaje_con_respuesta": 1, "porcentaje_destacadas": 1}},
                {"$sort": {"calificacion_promedio": -1}}
            ],
            "resumen_ciudad": [
                {"$group": {"_id": None,
                            "promedio_ciudad":     {"$avg": "$calificacion_promedio"},
                            "total_hoteles":        {"$sum": 1},
                            "total_resenas_ciudad": {"$sum": "$total_resenas"}}},
                {"$project": {"_id": 0,
                              "promedio_ciudad":     {"$round": ["$promedio_ciudad", 2]},
                              "total_hoteles": 1, "total_resenas_ciudad": 1}}
            ]
        }},
        {"$addFields": {"resumen_ciudad": {"$arrayElemAt": ["$resumen_ciudad", 0]}}},
        {"$addFields": {"hoteles": {"$map": {
            "input": "$hoteles", "as": "h",
            "in": {"$mergeObjects": ["$$h", {
                "por_debajo_promedio_ciudad": {"$lt": ["$$h.calificacion_promedio", "$resumen_ciudad.promedio_ciudad"]}
            }]}
        }}}}
    ]
    resultado = list(db["resenas"].aggregate(pipeline))
    if not resultado:
        raise HTTPException(404, f"No hay datos para la ciudad '{ciudad}'.")
    return {"ciudad": ciudad, **resultado[0]}