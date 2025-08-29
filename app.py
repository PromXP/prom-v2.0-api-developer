import uuid
from fastapi import  BackgroundTasks, Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect, BackgroundTasks, Query, Form, File, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from db import admin_lobby, build_admin_fhir_bundle, convert_patientbase_to_fhir, convert_patientmedical_to_fhir, convert_to_patientcontact_fhir_bundle,doctor_lobby, feedback_to_fhir_bundle, generate_fhir_bundle, generate_fhir_doctor_bundle, get_collection, post_surgery_to_fhir_bundle, users_collection,patient_base ,patient_contact ,patient_medical,patient_surgery_details,feedback,medical_left,medical_right
from models import Admin, Doctor, Feedback, PatientBase, PatientContact, PatientMedical, PostSurgeryDetail, QuestionnaireAssignment, QuestionnaireScore, QuestionnaireResetRequest, DeleteQuestionnaireRequest, LoginRequest, ResetPasswordRequest,FollowUpComment,PatientFull
from datetime import datetime, timezone
import boto3
from typing import Dict, List, Optional, Any
import re
from dotenv import load_dotenv
import os

# Load variables from .env file
load_dotenv()




app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"Message": "use '/docs' endpoint to find all the api related docs "}

now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

#DEVELOPER ROLE
#POST FUNCTIONS
@app.post("/registeradmin")
async def register_admin(admin: Admin):
    # Check if user already exists
    existing = await users_collection.find_one({
        "$or": [
            {"email": admin.email},
            {"phone_number": admin.phone_number},
            {"uhid": admin.uhid}
        ]
    })
    if existing:
        raise HTTPException(status_code=400, detail="User with this email, phone number, or UHID already exists.")

    # Store in admin_lobby (FHIR formatted)
    fhir_bundle = build_admin_fhir_bundle(admin)
    fhir_result = await admin_lobby.insert_one(fhir_bundle)

    # Store login credentials separately in users collection
    user_record = {
        "email": admin.email,
        "phone_number": admin.phone_number,
        "uhid": admin.uhid,
        "password": admin.password,
        "type": "admin",
        "created_at": now
    }
    await users_collection.insert_one(user_record)

    return {
        "message": "Admin registered successfully",
        "admin_id": str(fhir_result.inserted_id)
    }



#ADMIN ROLE
#POST FUNCTIONS
@app.post("/patients-base")
async def create_patient(patient: PatientBase):
    # Check if patient already exists in patient_base
    existing_patient = await patient_base.find_one({"id": patient.uhid})
    if existing_patient:
        raise HTTPException(status_code=400, detail="Patient already exists in patient_base")

    # Check if UHID already exists in Users
    existing_user = await users_collection.find_one({"uhid": patient.uhid})
    if existing_user:
        raise HTTPException(status_code=400, detail="UHID already exists in Users collection")

    # Convert to FHIR Bundle
    fhir_data = convert_patientbase_to_fhir(patient)

    # Store in patient_base collection
    await patient_base.insert_one(fhir_data)

    # Insert into Users collection
    user_doc = {
        "uhid": patient.uhid,
        "email": "",  # No email yet
        "phone": "",  # No phone yet
        "type": "patient",
        "created_at": datetime.utcnow().isoformat(),
        "password": patient.password
    }
    await users_collection.insert_one(user_doc)

    return {
        "message": "Patient created successfully and added to Users collection",
        "patient_id": patient.uhid
    }

@app.post("/fhir/store-patient-contact")
async def store_patient_contact(contact: PatientContact):
    # Check if email or phone already exists in Users for other UHID
    existing_email = await users_collection.find_one({"email": contact.email, "uhid": {"$ne": contact.uhid}})
    if existing_email:
        raise HTTPException(status_code=400, detail="Email already exists for another user")

    existing_phone = await users_collection.find_one({"phone": contact.phone_number, "uhid": {"$ne": contact.uhid}})
    if existing_phone:
        raise HTTPException(status_code=400, detail="Phone number already exists for another user")

    # Convert to FHIR Bundle
    fhir_bundle = convert_to_patientcontact_fhir_bundle(contact)

    # Store the FHIR Bundle in the database
    await patient_contact.insert_one(fhir_bundle)

    # Update Users collection with email and phone
    await users_collection.update_one(
        {"uhid": contact.uhid},
        {"$set": {
            "email": contact.email,
            "phone": contact.phone_number
        }}
    )

    return {
        "message": "Patient contact stored successfully and Users collection updated"
    }

@app.post("/fhir/store-patient-medical")
async def store_patient_medical(data: PatientMedical):
    fhir_bundle = convert_patientmedical_to_fhir(data)

    result = await patient_medical.insert_one(fhir_bundle)

    return {
        "status": "success" if result.inserted_id else "error",
        "uhid": data.uhid
    }

@app.post("/registerdoctor")
async def register_doctor(doctor: Doctor):
    # Check for duplicates
    existing = await users_collection.find_one({
        "$or": [
            {"email": doctor.email},
            {"phone_number": doctor.phone_number},
            {"uhid": doctor.uhid}
        ]
    })
    if existing:
        raise HTTPException(status_code=400, detail="User with this email, phone number, or UHID already exists.")

    # Check if admin who created this doctor exists
    admin_exists = await users_collection.find_one({
        "email": doctor.admin_created,
        "type": "admin"
    })
    if not admin_exists:
        raise HTTPException(status_code=404, detail="Admin who created this doctor does not exist.")

    # Generate and store FHIR-compliant doctor bundle
    doctor_bundle = generate_fhir_doctor_bundle(doctor)
    result = await doctor_lobby.insert_one(doctor_bundle)

    # Store in users collection
    user_record = {
        "email": doctor.email,
        "phone_number": doctor.phone_number,
        "uhid": doctor.uhid,
        "password": doctor.password,
        "type": "doctor",
        "created_at": now
    }
    await users_collection.insert_one(user_record)

    # Update doctors_created list in admin_lobby if needed
    await admin_lobby.update_one(
        {"entry.resource.identifier.value": doctor.admin_created},
        {"$push": {"doctors_created": doctor.email}}
    )

    return {
        "message": "Doctor registered successfully",
        "doctor_id": str(result.inserted_id)
    }

@app.post("/assign-questionnaire")
async def assign_questionnaire(data: QuestionnaireAssignment):
    collection = get_collection(data.side)

    # Find existing bundle by UHID in Patient resource's text.div (regex search)
    existing = await collection.find_one({
        "entry.resource.resourceType": "Patient",
        "entry.resource.text.div": {"$regex": data.uhid, "$options": "i"}
    })

    if existing:
        # Extract Patient UUID from existing bundle
        patient_uuid = None
        for entry in existing.get("entry", []):
            res = entry.get("resource", {})
            if res.get("resourceType") == "Patient":
                patient_uuid = res.get("id")
                break
        if not patient_uuid:
            # Defensive fallback: generate a new UUID (shouldn't happen)
            patient_uuid = str(uuid.uuid4())

        # Check for duplicate name+period in existing entries
        for entry in existing.get("entry", []):
            resource = entry.get("resource", {})
            if resource.get("resourceType") in ["Task", "QuestionnaireResponse", "Observation"]:
                if resource.get("resourceType") == "Observation":
                    code_text = resource.get("code", {}).get("text", "")
                    value_str = resource.get("valueString", "")
                    if (code_text == data.name and f"({data.period})" in value_str):
                        return {"message": "Questionnaire already assigned for this period"}
                else:
                    if resource.get("description") == f"{data.name} - {data.period}":
                        return {"message": "Questionnaire already assigned for this period"}

        # Generate new entries but exclude Patient resource
        new_bundle = generate_fhir_bundle([data], existing_patient_uuid=patient_uuid, patient_id=data.uhid.lower())
        new_entries = [
            e for e in new_bundle["entry"]
            if e["resource"]["resourceType"] != "Patient"
        ]

        # Append new entries to existing bundle's entry list
        await collection.update_one(
            {"_id": existing["_id"]},
            {"$push": {"entry": {"$each": new_entries}}}
        )

        return {"message": "Questionnaire assigned successfully"}

    else:
        # No existing bundle, create a new one with patient + questionnaire
        fhir_bundle = generate_fhir_bundle([data])
        await collection.insert_one(fhir_bundle)

        return {"message": "Questionnaire assigned successfully"}

@app.post("/assign-questionnaire-bulk")
async def assign_questionnaire_bulk(data: list[QuestionnaireAssignment]):  # Accept array
    results = []
    for q in data:  # Loop each questionnaire
        collection = get_collection(q.side)

        # Find existing bundle by UHID in Patient resource's text.div (regex search)
        existing = await collection.find_one({
            "entry.resource.resourceType": "Patient",
            "entry.resource.text.div": {"$regex": q.uhid, "$options": "i"}
        })

        if existing:
            # Extract Patient UUID
            patient_uuid = None
            for entry in existing.get("entry", []):
                res = entry.get("resource", {})
                if res.get("resourceType") == "Patient":
                    patient_uuid = res.get("id")
                    break
            if not patient_uuid:
                patient_uuid = str(uuid.uuid4())

            # Check duplicates
            already_assigned = False
            for entry in existing.get("entry", []):
                resource = entry.get("resource", {})
                if resource.get("resourceType") in ["Task", "QuestionnaireResponse", "Observation"]:
                    if resource.get("resourceType") == "Observation":
                        code_text = resource.get("code", {}).get("text", "")
                        value_str = resource.get("valueString", "")
                        if (code_text == q.name and f"({q.period})" in value_str):
                            already_assigned = True
                            break
                    else:
                        if resource.get("description") == f"{q.name} - {q.period}":
                            already_assigned = True
                            break

            if already_assigned:
                results.append({
                    "uhid": q.uhid,
                    "name": q.name,
                    "period": q.period,
                    "message": "Already exists"
                })
                continue

            # Generate new entries but exclude Patient resource
            new_bundle = generate_fhir_bundle([q], existing_patient_uuid=patient_uuid, patient_id=q.uhid.lower())
            new_entries = [
                e for e in new_bundle["entry"]
                if e["resource"]["resourceType"] != "Patient"
            ]

            await collection.update_one(
                {"_id": existing["_id"]},
                {"$push": {"entry": {"$each": new_entries}}}
            )
            results.append({
                "uhid": q.uhid,
                "name": q.name,
                "period": q.period,
                "message": "Assigned"
            })

        else:
            # No existing bundle -> create new
            fhir_bundle = generate_fhir_bundle([q])
            await collection.insert_one(fhir_bundle)
            results.append({
                "uhid": q.uhid,
                "name": q.name,
                "period": q.period,
                "message": "Assigned (new bundle)"
            })

    return {"results": results}


s3 = boto3.client(
    "s3",
    aws_access_key_id="AKIAQQ5O6E7YROLOVJGG",
    aws_secret_access_key="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",
    region_name="us-west-2",
)

@app.post("/upload-profile-photo")
async def upload_profile_photo(uhid: str = Form(...), usertype: str = Form(...), profile_image: UploadFile = File(...)):
    try:
        # ✅ Generate unique file name
        file_ext = profile_image.filename.split(".")[-1]
        unique_filename = f"{uhid}_{uuid.uuid4()}.{file_ext}"
        s3_key = f"PROM_PROFILE_IMAGES/{unique_filename}"

        # ✅ Upload to S3
        s3.upload_fileobj(
            profile_image.file,
            Bucket="blenderbuck",
            Key=s3_key,
            ExtraArgs={"ContentType": profile_image.content_type}
        )

        # ✅ Public URL
        profile_image_url = f"https://blenderbuck.s3.us-west-2.amazonaws.com/{s3_key}"

        # ✅ Choose collection based on usertype
        if usertype.lower() == "patient":
            collection = patient_contact
            resourceType = "Patient"
        elif usertype.lower() == "doctor":
            collection = doctor_lobby   # <-- make sure you import this from db.py
            resourceType = "Practitioner"
        else:
            raise HTTPException(status_code=400, detail="Invalid usertype. Use 'patient' or 'doctor'.")

        # ✅ Update the respective entry
        result = await collection.update_one(
            {
                "entry.resource.resourceType": resourceType,
                "entry.resource.identifier.value": uhid
            },
            {
                "$set": {
                    "entry.$[entryElem].resource.photo": [
                        {"url": profile_image_url}
                    ]
                }
            },
            array_filters=[{"entryElem.resource.resourceType": resourceType}]
        )

        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail=f"{usertype.title()} with UHID {uhid} not found")

        return {
            "message": f"{usertype.title()} profile photo uploaded successfully",
            "uhid": uhid,
            "profile_image_url": profile_image_url
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error uploading profile photo: {str(e)}")


#PUT FUNCTIONS
collections = {
    "patient_base": patient_base,
    "patient_contact": patient_contact,
    "patient_medical": patient_medical,
    "medical_left": medical_left,    # new collection
    "medical_right": medical_right,   # new collection
    "patient_surgery_details": patient_surgery_details,
    "patient_feedback": feedback,
}

@app.put("/patients/update/{uhid}")
async def update_patient(uhid: str, updates: Dict[str, str]):
    try:
        updated = False
        allowed_genders = {"male", "female", "other", "unknown"}
        date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")  # YYYY-MM-DD

        # --- Step 1: Check UHID conflict ---
        if "uhid" in updates:
            new_uhid = updates["uhid"]
            for name, coll in collections.items():
                conflict = await coll.find_one({
                    "$or": [
                        {"entry.resource.id": new_uhid},
                        {"entry.resource.identifier.value": new_uhid}
                    ]
                })
                if conflict:
                    raise HTTPException(
                        status_code=400,
                        detail=f"UHID '{new_uhid}' already exists in collection '{name}'"
                    )

        # --- Step 2: Iterate over all collections ---
        for name, coll in collections.items():
            document = await coll.find_one({
                "$or": [
                    {"entry.resource.id": uhid},
                    {"entry.resource.identifier.value": uhid},
                    {"entry.resource.text.div": {"$regex": uhid}},
                    {"entry.resource.subject.identifier.value": uhid},
                ]
            })
            if not document:
                continue

            for entry in document.get("entry", []):
                resource = entry.get("resource", {})
                res_type = resource.get("resourceType")

                # --- Update Patient resource ---
                if res_type == "Patient":
                    for key, value in updates.items():
                        key_lower = key.lower()

                        if key_lower == "uhid":
                            # Update identifier if exists
                            if "identifier" in resource and resource["identifier"]:
                                resource["identifier"][0]["value"] = value

                            # Update the Patient id
                            resource["id"] = value

                            # Update text.div in ALL collections including medical_left / medical_right
                            if "text" not in resource:
                                resource["text"] = {}
                            resource["text"]["div"] = f"<div xmlns=\"http://www.w3.org/1999/xhtml\"><p>Patient ID: {value}</p></div>"

                            updated = True


                        elif key_lower == "name":
                            parts = value.split(" ", 1)
                            given = parts[0]
                            family = parts[1] if len(parts) > 1 else ""
                            if "name" not in resource:
                                resource["name"] = [{}]
                            resource["name"][0]["given"] = [given]
                            resource["name"][0]["family"] = family
                            updated = True

                        elif key_lower == "given":
                            if "name" not in resource:
                                resource["name"] = [{}]
                            resource["name"][0]["given"] = [value]
                            updated = True

                        elif key_lower == "family":
                            if "name" not in resource:
                                resource["name"] = [{}]
                            resource["name"][0]["family"] = value
                            updated = True

                        elif key_lower in ["dob", "birthdate"]:
                            if not date_pattern.match(value):
                                raise HTTPException(
                                    status_code=400,
                                    detail="Invalid date format. Expected YYYY-MM-DD"
                                )
                            try:
                                datetime.strptime(value, "%Y-%m-%d")
                            except ValueError:
                                raise HTTPException(
                                    status_code=400,
                                    detail="Invalid date value. Example: 1985-07-28"
                                )
                            resource["birthDate"] = value
                            updated = True

                        elif key_lower == "gender":
                            if value.lower() not in allowed_genders:
                                raise HTTPException(
                                    status_code=400,
                                    detail=f"Invalid gender '{value}'. Allowed: {', '.join(allowed_genders)}"
                                )
                            resource["gender"] = value.lower()
                            updated = True

                                # --- Update Appointment resource ---
                elif res_type == "Appointment":
                    if "appointment_start" in updates:
                        try:
                            datetime.fromisoformat(updates["appointment_start"].replace("Z", "+00:00"))
                        except ValueError:
                            raise HTTPException(
                                status_code=400,
                                detail="Invalid datetime format for appointment_start. Use ISO 8601."
                            )
                        resource["start"] = updates["appointment_start"]
                        try:
                            datetime.fromisoformat(updates["appointment_start"].replace("Z", "+00:00"))
                        except ValueError:
                            raise HTTPException(
                                status_code=400,
                                detail="Invalid datetime format for appointment_end. Use ISO 8601."
                            )
                        resource["end"] = updates["appointment_start"]
                        updated = True


                                    # --- Update Observation resource ---
                
                elif res_type == "Observation":
                    code_text = resource.get("code", {}).get("text", "").lower()

                    # --- Feedback type observation ---
                    if code_text.startswith("patient feedback") and "uhid" in updates:
                        if "subject" in resource and "identifier" in resource["subject"]:
                            old_uhid = resource["subject"]["identifier"]["value"]
                            resource["subject"]["identifier"]["value"] = updates["uhid"]
                            updated = True
                            # Also update text.div if it contains UHID
                            if "text" in resource and "div" in resource["text"]:
                                resource["text"]["div"] = resource["text"]["div"].replace(old_uhid, updates["uhid"])

                    if code_text == "blood group" and "blood_group" in updates:
                        resource["valueString"] = updates["blood_group"]
                        resource["text"]["div"] = f"<div xmlns='http://www.w3.org/1999/xhtml'>Blood Group: {updates['blood_group']}</div>"
                        updated = True

                    elif code_text == "height" and "height" in updates:
                        resource["valueQuantity"]["value"] = float(updates["height"])
                        resource["text"]["div"] = f"<div xmlns='http://www.w3.org/1999/xhtml'>Height: {updates['height']} cm</div>"
                        updated = True

                    elif code_text == "weight" and "weight" in updates:
                        resource["valueQuantity"]["value"] = float(updates["weight"])
                        resource["text"]["div"] = f"<div xmlns='http://www.w3.org/1999/xhtml'>Weight: {updates['weight']} kg</div>"
                        updated = True
                    
                     # --- Activation Status and Activation Comment ---
                    
                    elif code_text == "activation status" and "activation_status" in updates:
                        # Update boolean value
                        resource["valueBoolean"] = updates["activation_status"] in ["true", "True", True, 1]
                        resource["text"]["div"] = (
                            f"<div xmlns='http://www.w3.org/1999/xhtml'>Activation Status: {resource['valueBoolean']}</div>"
                        )
                        updated = True

                        # If activation_comment is provided, always create a new Provenance entry
                        if "activation_comment" in updates and updates["activation_comment"]:
                            comment_text = updates["activation_comment"]

                            # Ensure subject reference exists
                            subject_ref = resource.get("subject", {}).get("reference", "Practitioner/unknown")

                            provenance_resource = {
                                "resourceType": "Provenance",
                                "id": str(uuid.uuid4()),
                                "target": [{"reference": f"urn:uuid:{resource.get('id', str(uuid.uuid4()))}"}],
                                "recorded": datetime.utcnow().isoformat() + "Z",
                                "activity": {"text": "Activation Comment"},
                                "agent": [{
                                    "type": {"text": "Practitioner"},
                                    "who": {"reference": subject_ref}
                                }],
                                "text": {
                                    "status": "generated",
                                    "div": f"<div xmlns='http://www.w3.org/1999/xhtml'>Comment: {comment_text}</div>"
                                }
                            }

                            # Append Provenance to the document entry list
                            if "entry" not in document:
                                document["entry"] = []
                            document["entry"].append({
                                "fullUrl": f"urn:uuid:{str(uuid.uuid4())}",
                                "resource": provenance_resource
                            })

                            updated = True

                    elif code_text == "patient current status" and "current_status" in updates:
                        resource["valueString"] = updates["current_status"]
                        resource["text"]["div"] = f"<div xmlns='http://www.w3.org/1999/xhtml'>Patient Current Status: {updates['current_status']}</div>"
                        updated = True

                    elif code_text == "surgery date left" and "surgery_date_left" in updates:
                        resource["valueString"] = updates["surgery_date_left"]
                        resource["text"]["div"] = f"<div xmlns='http://www.w3.org/1999/xhtml'>Surgery Date Left: {updates['surgery_date_left']}</div>"
                        updated = True
                    
                    elif code_text == "surgery date right" and "surgery_date_right" in updates:
                        resource["valueString"] = updates["surgery_date_right"]
                        resource["text"]["div"] = f"<div xmlns='http://www.w3.org/1999/xhtml'>Surgery Date Right: {updates['surgery_date_right']}</div>"
                        updated = True

                    elif code_text == "vip status" and "vip_status" in updates:
                        # Convert string/boolean input to proper boolean
                        vip_value = updates["vip_status"]
                        if isinstance(vip_value, str):
                            vip_value = vip_value.lower() in ["true", "1", "yes"]
                        resource["valueBoolean"] = vip_value

                        # Update the text.div
                        resource["text"]["div"] = (
                            f"<div xmlns='http://www.w3.org/1999/xhtml'>VIP Status: {'Yes' if vip_value else 'No'}</div>"
                        )
                        updated = True


                # --- Update Coverage resource ---
                elif res_type == "Coverage" and "funding_source" in updates:
                    resource["type"]["text"] = updates["funding_source"]
                    resource["text"]["div"] = f"<div xmlns='http://www.w3.org/1999/xhtml'>Funding Source: {updates['funding_source']}</div>"
                    updated = True

                # --- Update DocumentReference resource ---
                elif res_type == "DocumentReference":
                    doc_type = resource.get("type", {}).get("text", "").lower()

                    if doc_type == "aadhar" and "aadhar" in updates:
                        resource["content"][0]["attachment"]["title"] = updates["aadhar"]
                        resource["content"][0]["attachment"]["url"] = f"urn:idproof:Aadhar:{updates['aadhar']}"
                        resource["text"]["div"] = f"<div xmlns='http://www.w3.org/1999/xhtml'>AADHAR: {updates['aadhar']}</div>"
                        updated = True

                    elif doc_type == "pan" and "pan" in updates:
                        resource["content"][0]["attachment"]["title"] = updates["pan"]
                        resource["content"][0]["attachment"]["url"] = f"urn:idproof:PAN:{updates['pan']}"
                        resource["text"]["div"] = f"<div xmlns='http://www.w3.org/1999/xhtml'>PAN: {updates['pan']}</div>"
                        updated = True

                    elif doc_type == "passport" and "passport" in updates:
                        resource["content"][0]["attachment"]["title"] = updates["passport"]
                        resource["content"][0]["attachment"]["url"] = f"urn:idproof:Passport:{updates['passport']}"
                        resource["text"]["div"] = f"<div xmlns='http://www.w3.org/1999/xhtml'>PASSPORT: {updates['passport']}</div>"
                        updated = True

                # --- Update references in other resources (Observations etc.) ---
                for field in ["subject", "performer", "target"]:
                    if field in resource:
                        if isinstance(resource[field], dict) and resource[field].get("reference"):
                            ref = resource[field]["reference"]
                            if ref.endswith(uhid):
                                resource[field]["reference"] = ref.replace(uhid, updates.get("uhid", uhid))
                                updated = True
                        elif isinstance(resource[field], list):
                            for item in resource[field]:
                                ref = item.get("reference")
                                if ref and ref.endswith(uhid):
                                    item["reference"] = ref.replace(uhid, updates.get("uhid", uhid))
                                    updated = True

            # --- Save back the document if updated ---
            if updated:
                await coll.replace_one({"_id": document["_id"]}, document)
            
        # additionally update user_collection
        user_doc = await users_collection.find_one({"uhid": uhid})
        if user_doc:
            await users_collection.update_one(
                {"_id": user_doc["_id"]},
                {"$set": {"uhid": new_uhid}}
            )

        if not updated:
            raise HTTPException(status_code=400, detail="No valid fields to update")

        return {"message": "Patient updated successfully", "updated_fields": updates}

    

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating patient: {str(e)}")

@app.put("/update-doctor/{uhid}")
async def update_doctor_details(uhid: str, updates: Dict):
  
    coll = collections["patient_contact"]
    updated = False

    document = await coll.find_one({
        "$or": [
            {"entry.resource.id": uhid},
            {"entry.resource.identifier.value": uhid}
        ]
    })

    if not document:
        raise HTTPException(status_code=404, detail="Patient not found in Records")

    for entry in document.get("entry", []):
        resource = entry.get("resource", {})
        res_type = resource.get("resourceType")

        if res_type == "Practitioner":
            text_div = resource.get("text", {}).get("div", "")

            # --- Update Left Doctor ---
            if "doctor_left" in updates and "Left" in text_div:
                resource["identifier"][0]["value"] = updates["doctor_left"]
                
                updated = True

            # --- Update Right Doctor ---
            if "doctor_right" in updates and "Right" in text_div:
                resource["identifier"][0]["value"] = updates["doctor_right"]
                
                updated = True

    if updated:
        await coll.replace_one({"_id": document["_id"]}, document)
        return {"message": "Doctor details updated successfully"}

    raise HTTPException(status_code=404, detail="No doctor details updated")

@app.put("/reset_questionnaires")
async def reset_questionnaires(data: QuestionnaireResetRequest):
    # Select collection based on side
    if data.side == "left":
        collection = medical_left
    elif data.side == "right":
        collection = medical_right
    else:
        raise HTTPException(status_code=400, detail="side must be 'left' or 'right'")

    # Find bundle for this patient
    bundle = await collection.find_one({
        "entry.resource.resourceType": "Patient",
        "entry.resource.text.div": {"$regex": f"Patient ID: {data.patient_id}", "$options": "i"}
    })


    if not bundle:
        raise HTTPException(status_code=404, detail="Patient not found")

    # Update all observations inside bundle
    updated_entries = []
    for entry in bundle.get("entry", []):
        resource = entry.get("resource", {})
        if resource.get("resourceType") == "Observation":
            val_str = resource.get("valueString", "")
            if data.period not in val_str:
                # Skip observations that don’t match requested period
                updated_entries.append(entry)
                continue

            # Reset valueString
            resource["valueString"] = f"Scores ({data.period})"

            # Reset Completion Status component
            if "component" in resource:
                for comp in resource["component"]:
                    if comp.get("code", {}).get("text") == "Completion Status":
                        comp["valueBoolean"] = False

            # Reset end date
            if "effectivePeriod" in resource:
                resource["effectivePeriod"]["end"] = datetime.utcnow().strftime("%Y-%m-%d")

            # Reset text.div → erase only trailing score/recorded time
            if "text" in resource and "div" in resource["text"]:
                resource["text"]["div"] = re.sub(
                    r"(<p>)(.*?)(</p>)",
                    lambda m: m.group(1)
                    + re.sub(
                        r"\s*(?:,?\s*Completed:)?\s*\d*\s*(?:at\s*[0-9T:\-\.Z\+]+)?\s*$",
                        "",
                        m.group(2),
                        flags=re.IGNORECASE,
                    )
                    + m.group(3),
                    resource["text"]["div"],
                    flags=re.IGNORECASE | re.DOTALL,
                )

            # Reset notes → keep 4 items with empty text
            resource["note"] = [{"text": ""}, {"text": ""}, {"text": ""}, {"text": ""}]

        updated_entries.append(entry)

    # Save back to DB
    result = await collection.update_one(
        {"_id": bundle["_id"]},
        {"$set": {"entry": updated_entries}}
    )

    return {
        "status": "success",
        "modified_count": result.modified_count,
        "reset_period": data.period
    }


#GET FUNCTIONS
@app.get("/patient/{uhid}/photo")
async def get_patient_photo(uhid: str):
    # Get the FHIR bundle
    bundle = await patient_contact.find_one({"resourceType": "Bundle"})
    if not bundle:
        raise HTTPException(status_code=404, detail="Bundle not found")

    # Search inside entries
    for entry in bundle.get("entry", []):
        resource = entry.get("resource", {})
        if resource.get("resourceType") == "Patient":
            # Check identifiers for UHID match
            for identifier in resource.get("identifier", []):
                if identifier.get("value") == uhid:
                    photos = resource.get("photo", [])
                    if photos:
                        return {"profile_photo": photos[0].get("url")}
                    else:
                        raise HTTPException(status_code=404, detail="No profile photo found")

    raise HTTPException(status_code=404, detail="Patient with given UHID not found")

@app.get("/patients-by-uhid/{patient_uhid}")
async def get_patient_by_uhid(patient_uhid: str):
    try:
        all_patients_data=[]
        collections = {
                "patient_contact": patient_contact,
                "patient_base": patient_base,
                "patient_medical": patient_medical,
                "medical_left": medical_left,
                "medical_right": medical_right,
            }
        patient_data = {"uhid": patient_uhid, "collections": {}}

        for name, collection in collections.items():
                    query = {
                        "$and": [
                            {"resourceType": "Bundle"},
                            {
                                "$or": [
                                    {"entry.resource.identifier.value": patient_uhid},   # patient_contact
                                    {"entry.resource.id": patient_uhid},                 # patient_medical
                                    {"entry.resource.text.div": {"$regex": patient_uhid, "$options": "i"}}  # patient text.div
                                ]
                            },
                        ]
                    }

                    doc = await collection.find_one(query)

                    if doc:
                        doc["_id"] = str(doc["_id"])
                        # ✅ Call parser here before attaching
                # Pass side only for medical_left / medical_right
                        if name == "medical_left":
                            parsed = parse_patient_bundle(doc, side="Left")
                        elif name == "medical_right":
                            parsed = parse_patient_bundle(doc, side="Right")
                        else:
                            parsed = parse_patient_bundle(doc)
                        patient_data["collections"][name] = parsed

        all_patients_data.append(merge_clean_patient(patient_data))

        for patient in all_patients_data:
            # Left side
            left = patient.get("Medical_Left", {})
            total_left = 0
            completed_left = 0
            for prom_scores in left.values():
                for phase_data in prom_scores.values():
                    total_left += 1
                    if phase_data.get("completed"):
                        completed_left += 1
            pending_left = total_left - completed_left

            if total_left:
                patient["Medical_Left_Completion"] = round((completed_left / total_left) * 100, 2)
            else:
                patient["Medical_Left_Completion"] = "NA"

            patient["Medical_Left_Completed_Count"] = completed_left
            patient["Medical_Left_Pending_Count"] = pending_left

            # Right side
            right = patient.get("Medical_Right", {})
            total_right = 0
            completed_right = 0
            for prom_scores in right.values():
                for phase_data in prom_scores.values():
                    total_right += 1
                    if phase_data.get("completed"):
                        completed_right += 1
            pending_right = total_right - completed_right

            if total_right:
                patient["Medical_Right_Completion"] = round((completed_right / total_right) * 100, 2)
            else:
                patient["Medical_Right_Completion"] = "NA"

            patient["Medical_Right_Completed_Count"] = completed_right
            patient["Medical_Right_Pending_Count"] = pending_right


                    # ---------------- Compute OP status ----------------
        today = datetime.utcnow().date()

        def compute_phases(surgery_date_str):
                    if not surgery_date_str:
                        return "NA"
                    try:
                        surgery_date = datetime.strptime(surgery_date_str, "%Y-%m-%d").date()
                    except:
                        return "NA"

                    if surgery_date > today:
                        return "Pre Op"

                    delta_days = (today - surgery_date).days
                    phases = []
                    if delta_days >= 42:
                        phases.append("6W")
                    if delta_days >= 90:
                        phases.append("3M")
                    if delta_days >= 180:
                        phases.append("6M")
                    if delta_days >= 365:
                        phases.append("1Y")
                    if delta_days >= 730:
                        phases.append("2Y")
                    return phases or ["+6W"]

        surgery_left = patient.get("Medical", {}).get("surgery_date_left", None)
        surgery_right = patient.get("Medical", {}).get("surgery_date_right", None)
        patient["Patient_Status_Left"] = compute_phases(surgery_left)
        patient["Patient_Status_Right"] = compute_phases(surgery_right)

        return {
                    "patient": all_patients_data[0],
                }

    except Exception as e:
        raise HTTPException(
                status_code=500,
                detail={
                    "error": "Internal server error while fetching patients by admin UHID",
                    "exception": str(e),
                    "admin_uhid": patient_uhid,
                },
            )

@app.get("/patients-all-by-admin-uhid/{admin_uhid}")
async def get_all_patients_by_admin_uhid(admin_uhid: str):
    try:
        # Step 1: Find patients in patient_contact linked to admin
        patient_uhid = None   # Initialize each bundle
        matched_uhids = []

        bundle_cursor = patient_contact.find({"resourceType": "Bundle"})
        
        async for bundle in bundle_cursor:
            # Check if any Practitioner/Admin has this UHID
            admin_found = False
            # Step 1: check if Practitioner with given admin_uhid exists in this bundle
            for entry in bundle.get("entry", []):
                resource = entry.get("resource", {})
                if resource.get("resourceType") == "Practitioner":
                    for idf in resource.get("identifier", []):
                        if idf.get("value") == admin_uhid:
                            admin_found = True
                            break
                if admin_found:
                    break

            # Step 2: if admin found, search patient UHID in same bundle
            if admin_found:
                for entry in bundle.get("entry", []):
                    resource = entry.get("resource", {})
                    if resource.get("resourceType") == "Patient":
                        for identifier in resource.get("identifier", []):
                            system = identifier.get("system", "")
                            if "uhid" in system.lower():  # more flexible match
                                patient_uhid = identifier.get("value")
                                break
                    if patient_uhid:
                        break

            if patient_uhid:
                matched_uhids.append(patient_uhid)
                patient_uhid = None  # Reset for next bundle

        if not matched_uhids:
            raise HTTPException(
                status_code=404,
                detail=f"No patients found for admin UHID {admin_uhid}",
            )

        # Step 2: For each UHID, fetch from other collections
        all_patients_data = []

        collections = {
            "patient_contact": patient_contact,
            "patient_base": patient_base,
            "patient_medical": patient_medical,
            "medical_left": medical_left,
            "medical_right": medical_right,
        }

        for uhid in matched_uhids:
            patient_data = {"uhid": uhid, "collections": {}}

            for name, collection in collections.items():
                query = {
                    "$and": [
                        {"resourceType": "Bundle"},
                        {
                            "$or": [
                                {"entry.resource.identifier.value": uhid},   # patient_contact
                                {"entry.resource.id": uhid},                 # patient_medical
                                {"entry.resource.text.div": {"$regex": uhid, "$options": "i"}}  # patient text.div
                            ]
                        },
                    ]
                }

                doc = await collection.find_one(query)

                if doc:
                    doc["_id"] = str(doc["_id"])
                    # ✅ Call parser here before attaching
            # Pass side only for medical_left / medical_right
                    if name == "medical_left":
                        parsed = parse_patient_bundle(doc, side="Left")
                    elif name == "medical_right":
                        parsed = parse_patient_bundle(doc, side="Right")
                    else:
                        parsed = parse_patient_bundle(doc)
                    patient_data["collections"][name] = parsed

            all_patients_data.append(merge_clean_patient(patient_data))

            for patient in all_patients_data:
                # Left side
                left = patient.get("Medical_Left", {})
                total_left = 0
                completed_left = 0
                for prom_scores in left.values():
                    for phase_data in prom_scores.values():
                        total_left += 1
                        if phase_data.get("completed"):
                            completed_left += 1
                if total_left:
                    patient["Medical_Left_Completion"] = round((completed_left / total_left) * 100, 2)
                else:
                    patient["Medical_Left_Completion"] = "NA"

                # Right side
                right = patient.get("Medical_Right", {})
                total_right = 0
                completed_right = 0
                for prom_scores in right.values():
                    for phase_data in prom_scores.values():
                        total_right += 1
                        if phase_data.get("completed"):
                            completed_right += 1
                if total_right:
                    patient["Medical_Right_Completion"] = round((completed_right / total_right) * 100, 2)
                else:
                    patient["Medical_Right_Completion"] = "NA"

                # Optionally remove detailed left/right scores if you only want percentage
                patient.pop("Medical_Left", None)
                patient.pop("Medical_Right", None)

                # ---------------- Compute OP status ----------------
            today = datetime.utcnow().date()

            def compute_phases(surgery_date_str):
                if not surgery_date_str:
                    return "NA"
                try:
                    surgery_date = datetime.strptime(surgery_date_str, "%Y-%m-%d").date()
                except:
                    return "NA"

                if surgery_date > today:
                    return "Pre Op"

                delta_days = (today - surgery_date).days
                phases = []
                if delta_days >= 42:
                    phases.append("6W")
                if delta_days >= 90:
                    phases.append("3M")
                if delta_days >= 180:
                    phases.append("6M")
                if delta_days >= 365:
                    phases.append("1Y")
                if delta_days >= 730:
                    phases.append("2Y")
                return phases or ["+6W"]

            surgery_left = patient.get("Medical", {}).pop("surgery_date_left", None)
            surgery_right = patient.get("Medical", {}).pop("surgery_date_right", None)
            patient["Patient_Status_Left"] = compute_phases(surgery_left)
            patient["Patient_Status_Right"] = compute_phases(surgery_right)


        return {
            "admin_uhid": admin_uhid,
            "patients": all_patients_data,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "Internal server error while fetching patients by admin UHID",
                "exception": str(e),
                "admin_uhid": admin_uhid,
            },
        )

# def strip_div(xhtml: str) -> str:
#     if not xhtml:
#         return None
#     # remove <div ...> ... </div>
#     return re.sub(r"<[^>]+>", "", xhtml).strip()

def parse_patient_bundle(bundle,side="Left"):
    parsed = {
        "Patient": {},
        "Practitioners": {},
        "Appointments": [],
        "VIP_Status": None,
        "Medical": {
            "blood_group": None,
            "height": None,
            "weight": None,
            "activation_records": [],
            "follow_up_records": [],
            "patient_current_status": None,
            "surgery_date_left": None,
            "surgery_date_right": None,
            "funding_source": None,
            "id_proofs": {}
        },
        "Medical_Left": {     # NEW section
            "OKS": {},
            "SF12": {},
            "FJS": {},
            "KOOS_JR": {},
            "KSS": {}
        },
        "Medical_Right": {    # NEW section
            "OKS": {},
            "SF12": {},
            "FJS": {},
            "KOOS_JR": {},
            "KSS": {}
        }
    }

    for entry in bundle.get("entry", []):
        res = entry.get("resource", {})
        rtype = res.get("resourceType")

        # ---------------- Patient ----------------
        if rtype == "Patient":
            identifiers = res.get("identifier", [])
            uhid = None
            for idf in identifiers:
                if idf.get("system") == "http://hospital.smarthealth.org/uhid":
                    uhid = idf.get("value")

            # fallback to parsing from div if UHID not in identifier
            if not uhid and "div" in res.get("text", {}):
                div_text = res["text"]["div"]
                if "Patient ID:" in div_text:
                    uhid = div_text.split("Patient ID:")[-1].split("<")[0].strip()

            parsed["Patient"]["uhid"] = uhid or res.get("id")

                    # ---------------- Additional patient info ----------------
            parsed["Patient"]["name"] = None
            if "name" in res and isinstance(res["name"], list) and res["name"]:
                # Use the first name entry
                name_obj = res["name"][0]
                parts = []
                if "given" in name_obj:
                    parts.extend(name_obj["given"])
                if "family" in name_obj:
                    parts.append(name_obj["family"])
                parsed["Patient"]["name"] = " ".join(parts) if parts else None

            parsed["Patient"]["gender"] = res.get("gender")
            parsed["Patient"]["birthDate"] = res.get("birthDate")
            
            # contact info
            parsed["Patient"]["phone"] = None
            parsed["Patient"]["email"] = None
            for telecom in res.get("telecom", []):
                if telecom.get("system") == "phone":
                    parsed["Patient"]["phone"] = telecom.get("value")
                elif telecom.get("system") == "email":
                    parsed["Patient"]["email"] = telecom.get("value")

            # profile picture
            for photo in res.get("photo", []):
                parsed["Patient"]["photo"] = photo.get("url")

        # ---------------- Practitioners ----------------
        elif rtype == "Practitioner":
            role_name = res.get("text", {}).get("div", "")
            if "Left Doctor" in role_name:
                for ident in res.get("identifier", []):
                    parsed["Practitioners"]["left_doctor"] = ident.get("value")
            elif "Right Doctor" in role_name:
                for ident in res.get("identifier", []):
                    parsed["Practitioners"]["right_doctor"] = ident.get("value")
            elif "Admin Staff" in role_name:
                for ident in res.get("identifier", []):
                    parsed["Practitioners"]["admin_staff"] = ident.get("value")

        # ---------------- Appointments ----------------
        elif rtype == "Appointment":
            parsed["Appointments"].append({
                "start": res.get("start"),
                "end": res.get("end")
            })

        # ---------------- Observations ----------------
        elif rtype == "Observation":
            code_text = res.get("code", {}).get("text")

            # ---------- VIP / Basic Medical ----------
            if code_text == "VIP Status":
                parsed["VIP_Status"] = res.get("valueBoolean")

            elif code_text == "Blood Group":
                parsed["Medical"]["blood_group"] = res.get("valueString")

            elif code_text == "Height":
                val = res.get("valueQuantity", {}).get("value")
                unit = res.get("valueQuantity", {}).get("unit")
                parsed["Medical"]["height"] = f"{val} {unit}" if val and unit else None

            elif code_text == "Weight":
                val = res.get("valueQuantity", {}).get("value")
                unit = res.get("valueQuantity", {}).get("unit")
                parsed["Medical"]["weight"] = f"{val} {unit}" if val and unit else None

            # Check for Activation Status observations
            # elif code_text == "Activation Status":
            #     activation_status_value = res.get("valueBoolean")
            #     recorded = res.get("effectiveDateTime")  # or res.get("issued") depending on your FHIR
            #     parsed["Medical"].setdefault("activation_records", []).append({
            #         "activation_status": activation_status_value,
            #         "activation_comment": None,
            #         "recorded": recorded
            #     })

            elif code_text == "Patient Current Status":
                parsed["Medical"]["patient_current_status"] = res.get("valueString")

            elif code_text == "Surgery Date Left":
                parsed["Medical"]["surgery_date_left"] = res.get("valueString")

            elif code_text == "Surgery Date Right":
                parsed["Medical"]["surgery_date_right"] = res.get("valueString")

            # ---------- PROM Scores (Medical_Left / Medical_Right) ----------
            elif code_text in [
                "Oxford Knee Score (OKS)",
                "12-Item Short Form Survey (SF-12)",
                "Forgotten Joint Score (FJS)",
                "Knee injury and Osteoarthritis Outcome Score, JR (KOOS JR)",
                "Knee Society Score (KSS)"
            ]:
                phase = None
                val = res.get("valueString")
                completed = next(
                    (comp.get("valueBoolean") for comp in res.get("component", []) 
                    if comp.get("code", {}).get("text") == "Completion Status"),
                    None
                )
                # collect "note" texts if available
                notes = [n.get("text") for n in res.get("note", []) if n.get("text")]

                if val and "Pre Op" in val:
                    phase = "Pre_Op"
                elif val and "6W" in val:
                    phase = "6W"
                elif val and "3M" in val:
                    phase ="3M"
                elif val and "6M" in val:
                    phase ="6M"
                elif val and "1Y" in val:
                    phase ="1Y"
                elif val and "2Y" in val:
                    phase ="2Y"

                prom_key = None
                if "Oxford" in code_text:
                    prom_key = "OKS"
                elif "SF-12" in code_text:
                    prom_key = "SF12"
                elif "Forgotten Joint" in code_text:
                    prom_key = "FJS"
                elif "KOOS" in code_text:
                    prom_key = "KOOS_JR"
                elif "Knee Society" in code_text:
                    prom_key = "KSS"

                if prom_key and phase:
                    # Use side parameter to decide left/right
                    section = f"Medical_{side}"
                    if section not in parsed:
                        parsed[section] = {"OKS": {}, "SF12": {}, "FJS": {}, "KOOS_JR": {}, "KSS": {}}
                    parsed[section][prom_key][phase] = {
                        "score": val,
                        "completed": completed,
                        "other_notes": notes if notes else [],
                        "deadline": res.get("effectivePeriod",{}).get("end","")
                    }


        # ---------------- Provenance ----------------
        elif rtype == "Provenance":
            if res.get("activity", {}).get("text") == "Activation Comment":
                text_recorded = res.get("recorded")
                text_div = res.get("text", {}).get("div", "")
                comment_text = ""
                if "Comment:" in text_div:
                    comment_text = text_div.replace(
                        "<div xmlns='http://www.w3.org/1999/xhtml'>", ""
                    ).replace("</div>", "").replace("Comment:", "").strip()
                
                # Determine activation_status from comment
                activation_status = "activation" in comment_text.lower()
                
                # Remove "Activation - " prefix if present
                comment_text = comment_text.replace("Activation -", "").strip()
                
                parsed["Medical"].setdefault("activation_records", []).append({
                    "activation_status": activation_status,
                    "activation_comment": comment_text,
                    "recorded": text_recorded
                })
            
            if res.get("activity", {}).get("text") == "Patient Follow-up Comment":
                text_recorded = res.get("recorded")
                text_div = res.get("text", {}).get("div", "")
                comment_text = ""
                if "Comment:" in text_div:
                    comment_text = text_div.replace(
                        "<div xmlns='http://www.w3.org/1999/xhtml'>", ""
                    ).replace("</div>", "").replace("Follow-up Comment:", "").strip()
                
                # Determine activation_status from comment
                activation_status = "activation" in comment_text.lower()
                
                # Remove "Activation - " prefix if present
                comment_text = comment_text.replace("Activation -", "").strip()
                
                parsed["Medical"].setdefault("follow_up_records", []).append({
                    "follow_up_comment": comment_text,
                    "recorded": text_recorded
                })

            


        # ---------------- Coverage ----------------
        elif rtype == "Coverage":
            parsed["Medical"]["funding_source"] = res.get("type", {}).get("text")

        # ---------------- DocumentReference ----------------
        elif rtype == "DocumentReference":
            doc_type = res.get("type", {}).get("text")
            if doc_type:
                attachment = res.get("content", [{}])[0].get("attachment", {})
                parsed["Medical"]["id_proofs"][doc_type.lower()] = {
                    "number": attachment.get("title")
                }

    return parsed

def merge_clean_patient(patient: dict) -> dict:
    merged = {"uhid": patient.get("uhid"), "Patient": {}, "Practitioners": {}, "Appointments": [],
              "VIP_Status": None, "Medical": {}, "Medical_Left": {}, "Medical_Right": {}}

    for coll in patient.get("collections", {}).values():
        # Merge Patient info
        for key, val in coll.get("Patient", {}).items():
            if val not in [None, ""]:
                merged["Patient"][key] = val

        # Merge Practitioners
        for key, val in coll.get("Practitioners", {}).items():
            if val not in [None, ""]:
                merged["Practitioners"][key] = val

        # Merge Appointments
        for appt in coll.get("Appointments", []):
            if appt not in merged["Appointments"]:
                merged["Appointments"].append(appt)

        # VIP_Status: take first non-None
        if merged["VIP_Status"] is None and coll.get("VIP_Status") is not None:
            merged["VIP_Status"] = coll["VIP_Status"]

        # Merge Medical
        for key, val in coll.get("Medical", {}).items():
            if key == "activation_records":
                # Initialize merged list if not present
                if "activation_records" not in merged["Medical"]:
                    merged["Medical"]["activation_records"] = []
                # Add records without duplicates based on 'recorded'
                existing_timestamps = {rec["recorded"] for rec in merged["Medical"]["activation_records"]}
                for rec in val:
                    if rec.get("recorded") not in existing_timestamps:
                        merged["Medical"]["activation_records"].append(rec)
                        existing_timestamps.add(rec.get("recorded"))
            else:
                # Keep all lists, and non-empty dicts/values
                if isinstance(val, list) or val not in [None, {}, ""]:
                    merged["Medical"][key] = val

        # Merge Medical_Left PROM scores
        for prom, phases in coll.get("Medical_Left", {}).items():
            if prom not in merged["Medical_Left"]:
                merged["Medical_Left"][prom] = {}
            for phase, score in phases.items():
                if score.get("score") or score.get("completed"):
                    merged["Medical_Left"][prom][phase] = score

        # Merge Medical_Right PROM scores
        for prom, phases in coll.get("Medical_Right", {}).items():
            if prom not in merged["Medical_Right"]:
                merged["Medical_Right"][prom] = {}
            for phase, score in phases.items():
                if score.get("score") or score.get("completed"):
                    merged["Medical_Right"][prom][phase] = score

    # Remove empty sections
    if not merged["Practitioners"]:
        merged.pop("Practitioners")
    if not merged["Appointments"]:
        merged.pop("Appointments")
    if not merged["Medical_Left"]:
        merged.pop("Medical_Left")
    if not merged["Medical_Right"]:
        merged.pop("Medical_Right")
    if merged["VIP_Status"] is None:
        merged.pop("VIP_Status")
    if not merged["Medical"]:
        merged.pop("Medical")

    return merged

@app.get("/getalldoctors")
async def get_all_doctors():
    doctors_cursor = doctor_lobby.find()  # fetch all documents
    doctors = []
    async for doc in doctors_cursor:
        doc["_id"] = str(doc["_id"])  # convert ObjectId to string
        parsed = parse_practitioner_bundle(doc)
        doctors.append(parsed)

    if not doctors:
        raise HTTPException(status_code=404, detail="No doctors found")

    return doctors

def parse_practitioner_bundle(bundle: dict) -> dict:
    """
    Parse a Practitioner + PractitionerRole bundle into a structured doctor record.
    """
    parsed = {

        "name": None,
        "gender": None,
        "birth_date": None,
        "email": None,
        "phone": None,
        "uhid": None,
        "council_number": None,
        "blood_group": None,
        "specialization": None,
        "photo_url": None,
        "admin_created": None
    }

    entries = bundle.get("entry", [])
    practitioner = None
    role = None

    # Identify practitioner and role
    for entry in entries:
        res = entry.get("resource", {})
        if res.get("resourceType") == "Practitioner":
            practitioner = res
        elif res.get("resourceType") == "PractitionerRole":
            role = res

    if practitioner:
        parsed["name"] = practitioner.get("name", [{}])[0].get("text")
        parsed["gender"] = practitioner.get("gender")
        parsed["birth_date"] = practitioner.get("birthDate")

        # Extract telecom (email & phone)
        for t in practitioner.get("telecom", []):
            if t.get("system") == "email":
                parsed["email"] = t.get("value")
            elif t.get("system") == "phone":
                parsed["phone"] = t.get("value")

        # Extract identifiers (UHID, council number, etc.)
        for ident in practitioner.get("identifier", []):
            if ident.get("system") == "http://hospital.org/uhid":
                parsed["uhid"] = ident.get("value")
            if ident.get("system") == "http://hospital.smarthealth.org/doctor-council-number":
                parsed["council_number"] = ident.get("value")

        # Photo
        if practitioner.get("photo"):
            parsed["photo_url"] = practitioner["photo"][0].get("url")

    if role:
        # Specialization
        if role.get("code"):
            parsed["specialization"] = role["code"][0].get("text")

        # Identifiers from role
        for ident in role.get("identifier", []):
            if ident.get("system") == "http://hospital.org/bloodgroup":
                parsed["blood_group"] = ident.get("value")
            if ident.get("system") == "http://hospital.org/admin-created":
                parsed["admin_created"] = ident.get("value")

    return parsed



#PATCH FUNCTIONS
@app.patch("/patients/add-followup")
async def add_followup(comment_data: FollowUpComment):
    uhid = comment_data.uhid
    comment_text = comment_data.comment

    # Find the patient's Observation for activation_status or relevant target
    patient_bundle = await patient_medical.find_one({"entry.resource.id": uhid})
    if not patient_bundle:
        raise HTTPException(status_code=404, detail="Patient not found")

    # Create Provenance resource
    provenance = {
        "fullUrl": f"urn:uuid:{str(uuid.uuid4())}",
        "resource": {
            "resourceType": "Provenance",
            "id": str(uuid.uuid4()),
            "target": [
                {
                    "reference": f"urn:uuid:{str(uuid.uuid4())}"
                }
            ],
            "recorded": datetime.utcnow().isoformat() + "Z",
            "activity": {
                "text": "Patient Follow-up Comment"
            },
            "agent": [
                {
                    "type": {"text": "Practitioner"},
                    "who": {"reference": f"urn:uuid:{str(uuid.uuid4())}"}  # can change to actual practitioner
                }
            ],
            "text": {
                "status": "generated",
                "div": f"<div xmlns='http://www.w3.org/1999/xhtml'>Follow-up Comment: {comment_text}</div>"
            }
        }
    }

    # Append Provenance to the bundle
    await patient_medical.update_one(
        {"entry.resource.id": uhid},
        {"$push": {"entry": provenance}}
    )

    return {"status": "success", "message": "Follow-up comment added"}


#DELETE FUNCTIONS
@app.delete("/delete-questionnaires")
async def delete_questionnaires(data: DeleteQuestionnaireRequest):
    # Select collection based on side
    if data.side == "left":
        collection = medical_left
    elif data.side == "right":
        collection = medical_right
    else:
        raise HTTPException(status_code=400, detail="side must be 'left' or 'right'")

    # Find bundle for this patient
    bundle = await collection.find_one({
        "entry.resource.resourceType": "Patient",
        "entry.resource.text.div": {"$regex": f"Patient ID: {data.patient_id}", "$options": "i"}
    })

    if not bundle:
        raise HTTPException(status_code=404, detail="Patient not found")

    updated_entries = []
    deleted_count = 0

    for entry in bundle.get("entry", []):
        resource = entry.get("resource", {})
        if resource.get("resourceType") == "Observation":
            div_text = resource.get("text", {}).get("div", "")
            # Check if period string is inside the div
            if data.period in div_text:
                deleted_count += 1
                continue  # Skip adding this entry (delete it)
        updated_entries.append(entry)

    if deleted_count == 0:
        raise HTTPException(status_code=404, detail="No questionnaires matched for this period")

    # Update DB with remaining entries
    await collection.update_one(
        {"_id": bundle["_id"]},
        {"$set": {"entry": updated_entries}}
    )

    return {"message": f"Deleted {deleted_count} questionnaires for period '{data.period}'"}


#HELPER ENDPOINTS
@app.post("/patients/full")
async def create_full_patient(data: PatientFull):
    # ===== BASE SECTION =====
    patient = data.base
    existing_patient = await patient_base.find_one({"id": patient.uhid})
    if existing_patient:
        raise HTTPException(status_code=400, detail="Patient already exists in patient_base")

    existing_user = await users_collection.find_one({"uhid": patient.uhid})
    if existing_user:
        raise HTTPException(status_code=400, detail="UHID already exists in Users collection")

    fhir_data = convert_patientbase_to_fhir(patient)
    await patient_base.insert_one(fhir_data)

    user_doc = {
        "uhid": patient.uhid,
        "email": "",  
        "phone": "",  
        "type": "patient",
        "created_at": datetime.utcnow().isoformat(),
        "password": patient.password
    }
    await users_collection.insert_one(user_doc)

    # ===== CONTACT SECTION =====
    contact = data.contact
    existing_email = await users_collection.find_one({"email": contact.email, "uhid": {"$ne": contact.uhid}})
    if existing_email:
        raise HTTPException(status_code=400, detail="Email already exists for another user")

    existing_phone = await users_collection.find_one({"phone": contact.phone_number, "uhid": {"$ne": contact.uhid}})
    if existing_phone:
        raise HTTPException(status_code=400, detail="Phone number already exists for another user")

    fhir_bundle_contact = convert_to_patientcontact_fhir_bundle(contact)
    await patient_contact.insert_one(fhir_bundle_contact)

    await users_collection.update_one(
        {"uhid": contact.uhid},
        {"$set": {"email": contact.email, "phone": contact.phone_number}}
    )

    # ===== MEDICAL SECTION =====
    medical = data.medical
    fhir_bundle_medical = convert_patientmedical_to_fhir(medical)
    await patient_medical.insert_one(fhir_bundle_medical)

    # ===== RESPONSE =====
    return {
        "message": "Patient fully created with base, contact, and medical details",
        "uhid": patient.uhid
    }

@app.get("/get_admin_patient_reminder_page/{patient_uhid}")
async def get_admin_patient_reminder_page(patient_uhid: str):
    try:
        all_patients_data=[]
        collections = {
                "patient_contact": patient_contact,
                "patient_medical": patient_medical,
                "medical_left": medical_left,
                "medical_right": medical_right,
            }
        patient_data = {"uhid": patient_uhid, "collections": {}}

        for name, collection in collections.items():
                    query = {
                        "$and": [
                            {"resourceType": "Bundle"},
                            {
                                "$or": [
                                    {"entry.resource.identifier.value": patient_uhid},   # patient_contact
                                    {"entry.resource.id": patient_uhid},                 # patient_medical
                                    {"entry.resource.text.div": {"$regex": patient_uhid, "$options": "i"}}  # patient text.div
                                ]
                            },
                        ]
                    }

                    doc = await collection.find_one(query)

                    if doc:
                        doc["_id"] = str(doc["_id"])
                        # ✅ Call parser here before attaching
                # Pass side only for medical_left / medical_right
                        if name == "medical_left":
                            parsed = parse_patient_bundle(doc, side="Left")
                        elif name == "medical_right":
                            parsed = parse_patient_bundle(doc, side="Right")
                        else:
                            parsed = parse_patient_bundle(doc)
                        patient_data["collections"][name] = parsed

        all_patients_data.append(merge_clean_patient(patient_data))

        for patient in all_patients_data:
                    # Left side
                    left = patient.get("Medical_Left", {})
                    total_left = 0
                    completed_left = 0
                    for prom_scores in left.values():
                        for phase_data in prom_scores.values():
                            total_left += 1
                            if phase_data.get("completed"):
                                completed_left += 1
                    if total_left:
                        patient["Medical_Left_Completion"] = round((completed_left / total_left) * 100, 2)
                    else:
                        patient["Medical_Left_Completion"] = "NA"

                    # Right side
                    right = patient.get("Medical_Right", {})
                    total_right = 0
                    completed_right = 0
                    for prom_scores in right.values():
                        for phase_data in prom_scores.values():
                            total_right += 1
                            if phase_data.get("completed"):
                                completed_right += 1
                    if total_right:
                        patient["Medical_Right_Completion"] = round((completed_right / total_right) * 100, 2)
                    else:
                        patient["Medical_Right_Completion"] = "NA"

                    # ---------------- Compute OP status ----------------
        today = datetime.utcnow().date()

        def compute_phases(surgery_date_str):
                    if not surgery_date_str:
                        return "NA"
                    try:
                        surgery_date = datetime.strptime(surgery_date_str, "%Y-%m-%d").date()
                    except:
                        return "NA"

                    if surgery_date > today:
                        return "Pre Op"

                    delta_days = (today - surgery_date).days
                    phases = []
                    if delta_days >= 42:
                        phases.append("6W")
                    if delta_days >= 90:
                        phases.append("3M")
                    if delta_days >= 180:
                        phases.append("6M")
                    if delta_days >= 365:
                        phases.append("1Y")
                    if delta_days >= 730:
                        phases.append("2Y")
                    return phases or ["+6W"]

        surgery_left = patient.get("Medical", {}).pop("surgery_date_left", None)
        surgery_right = patient.get("Medical", {}).pop("surgery_date_right", None)
        patient["Patient_Status_Left"] = compute_phases(surgery_left)
        patient["Patient_Status_Right"] = compute_phases(surgery_right)

        patient = all_patients_data[0]

        def filter_pending_questionnaires(medical_side: dict):
            pending = {}
            for questionnaire, phases in medical_side.items():
                pending_phases = {
                    phase: data
                    for phase, data in phases.items()
                    if not data.get("completed", False)  # ✅ keep only pending
                }
                if pending_phases:
                    pending[questionnaire] = pending_phases
            return pending

        # Keep only the needed fields
        clean_patient = {
            "uhid": patient.get("uhid"),
            "Patient": {
                "phone": patient.get("Patient", {}).get("phone"),
                "email": patient.get("Patient", {}).get("email"),
            },
            "Medical_Left": filter_pending_questionnaires(patient.get("Medical_Left", {})),
            "Medical_Right": filter_pending_questionnaires(patient.get("Medical_Right", {})),
            "follow_up_records": patient.get("Medical", {}).get("follow_up_records", [])
        }

        return {
            "patient": clean_patient,
        }

    except Exception as e:
        raise HTTPException(
                status_code=500,
                detail={
                    "error": "Internal server error while fetching patients by admin UHID",
                    "exception": str(e),
                    "admin_uhid": patient_uhid,
                },
            )

@app.get("/get_admin_patient_activation_page/{patient_uhid}")
async def get_admin_patient_activation_page(patient_uhid: str):
    try:
        all_patients_data=[]
        collections = {
                "patient_contact": patient_contact,
                "patient_medical": patient_medical,
                "medical_left": medical_left,
                "medical_right": medical_right,
            }
        patient_data = {"uhid": patient_uhid, "collections": {}}

        for name, collection in collections.items():
                    query = {
                        "$and": [
                            {"resourceType": "Bundle"},
                            {
                                "$or": [
                                    {"entry.resource.identifier.value": patient_uhid},   # patient_contact
                                    {"entry.resource.id": patient_uhid},                 # patient_medical
                                    {"entry.resource.text.div": {"$regex": patient_uhid, "$options": "i"}}  # patient text.div
                                ]
                            },
                        ]
                    }

                    doc = await collection.find_one(query)

                    if doc:
                        doc["_id"] = str(doc["_id"])
                        # ✅ Call parser here before attaching
                # Pass side only for medical_left / medical_right
                        if name == "medical_left":
                            parsed = parse_patient_bundle(doc, side="Left")
                        elif name == "medical_right":
                            parsed = parse_patient_bundle(doc, side="Right")
                        else:
                            parsed = parse_patient_bundle(doc)
                        patient_data["collections"][name] = parsed

        all_patients_data.append(merge_clean_patient(patient_data))

        for patient in all_patients_data:
                    # Left side
                    left = patient.get("Medical_Left", {})
                    total_left = 0
                    completed_left = 0
                    for prom_scores in left.values():
                        for phase_data in prom_scores.values():
                            total_left += 1
                            if phase_data.get("completed"):
                                completed_left += 1
                    if total_left:
                        patient["Medical_Left_Completion"] = round((completed_left / total_left) * 100, 2)
                    else:
                        patient["Medical_Left_Completion"] = "NA"

                    # Right side
                    right = patient.get("Medical_Right", {})
                    total_right = 0
                    completed_right = 0
                    for prom_scores in right.values():
                        for phase_data in prom_scores.values():
                            total_right += 1
                            if phase_data.get("completed"):
                                completed_right += 1
                    if total_right:
                        patient["Medical_Right_Completion"] = round((completed_right / total_right) * 100, 2)
                    else:
                        patient["Medical_Right_Completion"] = "NA"

                    # ---------------- Compute OP status ----------------
        today = datetime.utcnow().date()

        def compute_phases(surgery_date_str):
                    if not surgery_date_str:
                        return "NA"
                    try:
                        surgery_date = datetime.strptime(surgery_date_str, "%Y-%m-%d").date()
                    except:
                        return "NA"

                    if surgery_date > today:
                        return "Pre Op"

                    delta_days = (today - surgery_date).days
                    phases = []
                    if delta_days >= 42:
                        phases.append("6W")
                    if delta_days >= 90:
                        phases.append("3M")
                    if delta_days >= 180:
                        phases.append("6M")
                    if delta_days >= 365:
                        phases.append("1Y")
                    if delta_days >= 730:
                        phases.append("2Y")
                    return phases or ["+6W"]

        surgery_left = patient.get("Medical", {}).pop("surgery_date_left", None)
        surgery_right = patient.get("Medical", {}).pop("surgery_date_right", None)
        patient["Patient_Status_Left"] = compute_phases(surgery_left)
        patient["Patient_Status_Right"] = compute_phases(surgery_right)

        patient = all_patients_data[0]

        def filter_pending_questionnaires(medical_side: dict):
            pending = {}
            for questionnaire, phases in medical_side.items():
                pending_phases = {
                    phase: data
                    for phase, data in phases.items()
                    if not data.get("completed", False)  # ✅ keep only pending
                }
                if pending_phases:
                    pending[questionnaire] = pending_phases
            return pending

        # Keep only the needed fields
        clean_patient = {
            "uhid": patient.get("uhid"),
            "activation_records": patient.get("Medical", {}).get("activation_records", [])
        }

        return {
            "patient": clean_patient,
        }

    except Exception as e:
        raise HTTPException(
                status_code=500,
                detail={
                    "error": "Internal server error while fetching patients by admin UHID",
                    "exception": str(e),
                    "admin_uhid": patient_uhid,
                },
            )

@app.get("/get_admin_doctor_page")
async def get_admin_doctor_page():
    try:
        doctors_cursor = doctor_lobby.find()  # fetch all documents
        doctors = []
        async for doc in doctors_cursor:
            doc["_id"] = str(doc["_id"])  # convert ObjectId to string
            parsed = parse_practitioner_bundle(doc)
            doc=parsed
            
            clean_doc = {
                "name": parsed.get("name"),
                "birth_date": parsed.get("birth_date"),
                "gender": parsed.get("gender"),
                "uhid": parsed.get("uhid"),
            }

            # Step 1: Find patients in patient_contact linked to admin
            patient_uhid = None   # Initialize each bundle
            matched_patients = []
            cleaned_matched_data = []

            bundle_cursor = patient_contact.find({"resourceType": "Bundle"})

            async for bundle in bundle_cursor:
                admin_found = False
                patient_obj = None
                patient_uhid = None

                # Step 1: check if Practitioner with given doctor_uhid exists in this bundle
                for entry in bundle.get("entry", []):
                    resource = entry.get("resource", {})
                    if resource.get("resourceType") == "Practitioner":
                        for idf in resource.get("identifier", []):
                            if idf.get("value") == parsed.get("uhid"):
                                admin_found = True
                                
                                break
                    if admin_found:
                        break

                # Step 2: if admin found, search patient UHID + leg status in same bundle
                if admin_found:
                    for entry in bundle.get("entry", []):
                        resource = entry.get("resource", {})

                        if resource.get("resourceType") == "Patient":
                                    for identifier in resource.get("identifier", []):
                                        system = identifier.get("system", "")
                                        if "uhid" in system.lower():
                                            patient_uhid = identifier.get("value")
                                            patient_obj = {
                                                "uhid": patient_uhid,
                                                "leg_left": None,
                                                "leg_right": None,
                                            }

                        elif resource.get("resourceType") == "Practitioner":
                            identifiers = resource.get("identifier", [])

                            if patient_obj:
                                text_div = resource.get("text", {}).get("div", "").lower()
                            
                                # Loop through identifiers
                                for identifier in identifiers:
                                    doctor_value = identifier.get("value", "")

                                    if "left" in text_div and doctor_value == parsed.get("uhid"):
                                        patient_obj["leg_left"] = "Assigned"
                                    elif "right" in text_div and doctor_value == parsed.get("uhid"):
                                        patient_obj["leg_right"] = "Assigned"


                    if patient_obj:
                        matched_patients.append(patient_obj)

            if not matched_patients:
                clean_doc["total_patients"]=0
                clean_doc["overall_compliance"]="NA"


            # Step 2: For each UHID, fetch from other collections
            all_patients_data = []

            collections = {
                "patient_contact": patient_contact,
                "patient_base": patient_base,
                "patient_medical": patient_medical,
                "medical_left": medical_left,
                "medical_right": medical_right,
            }

            for uhid in matched_patients:
                patient_data = {"uhid": uhid.get("uhid"), "collections": {}}

                for name, collection in collections.items():
                    query = {
                        "$and": [
                            {"resourceType": "Bundle"},
                            {
                                "$or": [
                                    {"entry.resource.identifier.value": uhid.get("uhid")},   # patient_contact
                                    {"entry.resource.id": uhid.get("uhid")},                 # patient_medical
                                    {"entry.resource.text.div": {"$regex": uhid.get("uhid"), "$options": "i"}}  # patient text.div
                                ]
                            },
                        ]
                    }

                    doc = await collection.find_one(query)

                    if doc:
                        doc["_id"] = str(doc["_id"])
                        # ✅ Call parser here before attaching
                # Pass side only for medical_left / medical_right
                        if name == "medical_left":
                            parsed = parse_patient_bundle(doc, side="Left")
                        elif name == "medical_right":
                            parsed = parse_patient_bundle(doc, side="Right")
                        else:
                            parsed = parse_patient_bundle(doc)
                        patient_data["collections"][name] = parsed

                        # ---- Merge clean structure ----
                patient = merge_clean_patient(patient_data)

                # ---- Compute completion % ----
                if uhid.get("leg_left") == "Assigned":
                    left = patient.get("Medical_Left", {})
                    total_left, completed_left = 0, 0
                    for prom_scores in left.values():
                        for phase_data in prom_scores.values():
                            total_left += 1
                            if phase_data.get("completed"):
                                completed_left += 1
                    patient["Medical_Left_Completion"] = (
                        round((completed_left / total_left) * 100, 2) if total_left else "NA"
                    )

                if uhid.get("leg_right") == "Assigned":
                    right = patient.get("Medical_Right", {})
                    total_right, completed_right = 0, 0
                    for prom_scores in right.values():
                        for phase_data in prom_scores.values():
                            total_right += 1
                            if phase_data.get("completed"):
                                completed_right += 1
                    patient["Medical_Right_Completion"] = (
                        round((completed_right / total_right) * 100, 2) if total_right else "NA"
                    )

                # ---- Remove raw details if only % needed ----
                patient.pop("Medical_Left", None)
                patient.pop("Medical_Right", None)

                # ---- Compute OP phases ----
                today = datetime.utcnow().date()

                def compute_phases(surgery_date_str):
                    if not surgery_date_str:
                        return "NA"
                    try:
                        surgery_date = datetime.strptime(surgery_date_str, "%Y-%m-%d").date()
                    except:
                        return "NA"

                    if surgery_date > today:
                        return "Pre Op"

                    delta_days = (today - surgery_date).days
                    phases = []
                    if delta_days >= 42: phases.append("6W")
                    if delta_days >= 90: phases.append("3M")
                    if delta_days >= 180: phases.append("6M")
                    if delta_days >= 365: phases.append("1Y")
                    if delta_days >= 730: phases.append("2Y")
                    return phases or ["+6W"]

                surgery_left = patient.get("Medical", {}).pop("surgery_date_left", None)
                surgery_right = patient.get("Medical", {}).pop("surgery_date_right", None)

                if uhid.get("leg_left") == "Assigned":
                    patient["Patient_Status_Left"] = compute_phases(surgery_left)
                if uhid.get("leg_right") == "Assigned":
                    patient["Patient_Status_Right"] = compute_phases(surgery_right)

                # ---- Finally append ----
                all_patients_data.append(patient)

            result = None

            total = 0
            count = 0

            for patient in all_patients_data:
                left = patient.get("Medical_Left_Completion")
                if left not in (None, "NA"):
                    total += left
                    count += 1

                right = patient.get("Medical_Right_Completion")
                if right not in (None, "NA"):
                    total += right
                    count += 1

            overall = round(total / count, 2) if count > 0 else "NA"

            result = overall

            clean_doc["total_patients"]=len(all_patients_data)
            clean_doc["overall_compliance"]=result


            doctors.append(clean_doc)

        if not doctors:
            raise HTTPException(status_code=404, detail="No doctors found")

        

        return {
            "total_doctors": doctors
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "Internal server error while fetching patients by doctors",
                "exception": str(e)
            },
        )

@app.get("/getdoctorname/{uhid}")
async def get_doctor_name(uhid: str):
    # find doctor bundle containing given UHID
    doc = await doctor_lobby.find_one({
        "entry.resource.identifier": {
            "$elemMatch": {
                "system": "http://hospital.org/uhid",
                "value": uhid
            }
        }
    })

    if not doc:
        return {"uhid": uhid, "name": "NA"}

    # loop through entries to find Practitioner resource
    doctor_name = "NA"
    for entry in doc.get("entry", []):
        resource = entry.get("resource", {})
        if resource.get("resourceType") == "Practitioner":
            names = resource.get("name", [])
            if names and "text" in names[0]:
                doctor_name = names[0]["text"]
                break

    return {"uhid": uhid, "name": doctor_name}





#PATIENT ROLE
#POST FUNCTIONS
@app.post("/feedback/fhir")
async def post_feedback_fhir(details: Feedback):
    # Convert Feedback details into FHIR Bundle
    fhir_bundle = feedback_to_fhir_bundle(details)

    # Convert FHIR Bundle to JSON-serializable format
    fhir_bundle_json = jsonable_encoder(fhir_bundle)

    # Insert FHIR Bundle into MongoDB
    result = feedback.insert_one(fhir_bundle_json)

    return {
        "message": "FHIR bundle stored successfully",

    }


#PUT FUNCTIONS
@app.put("/add-score")
async def add_score(data: QuestionnaireScore):
    collection = get_collection(data.side)

    # Find patient bundle by UHID in Patient resource's text.div
    bundle = await collection.find_one({
        "entry.resource.resourceType": "Patient",
        "entry.resource.text.div": {"$regex": data.uhid, "$options": "i"}
    })

    if not bundle:
        raise HTTPException(status_code=404, detail="Patient bundle not found")

    updated = False
    for entry in bundle.get("entry", []):
        res = entry.get("resource", {})
        if (
            res.get("resourceType") == "Observation"
            and res.get("subject", {}).get("reference", "").startswith("urn:uuid:")
            and res.get("code", {}).get("text") == data.name
            and f"({data.period})" in res.get("valueString", "")
        ):
            # ✅ Update score display with timestamp
            res["valueString"] = f"Scores ({data.period}): {', '.join(str(s) for s in data.score)} (Recorded at {data.timestamp})"

            # ✅ Update Observation status
            res["status"] = "final"

            # ✅ Update Completion Status to true
            for comp in res.get("component", []):
                if comp.get("code", {}).get("text") == "Completion Status":
                    comp["valueBoolean"] = True
                    break

            # ✅ Add others as Observation.note
            if data.others:
                res["note"] = [{"text": note} for note in data.others]

            # ✅ Update narrative text
            res["text"]["div"] = (
                f'<div xmlns="http://www.w3.org/1999/xhtml">'
                f'<p>{data.name} Scores ({data.period}), Completed: 1 at {data.timestamp}</p></div>'
            )

            updated = True
            break

    if not updated:
        raise HTTPException(status_code=404, detail="Observation not found for update")

    await collection.update_one({"_id": bundle["_id"]}, {"$set": {"entry": bundle["entry"]}})

    return {"message": "Score, timestamp, and additional notes updated successfully"}


#GET FUNCTIONS


#DELETE FUNCTIONS




#NOT USED
# @app.post("/post_patient/")
# async def post_patient_to_db(patient: Patient):
#     try:
#         fhir_patient = convert_patient_to_fhir(patient)  # corrected function name
#         patient_data.insert_one(fhir_patient)
#         return {
#             "status": "success",
#             "message": "Patient data saved in FHIR format."
#         }
#     except Exception as e:
#         import traceback
#         print(traceback.format_exc())
#         raise HTTPException(status_code=500, detail=str(e))





#DOCTOR ROLE
#POST FUNCTIONS
@app.post("/surgery_details")
async def create_surgery_details(details: PostSurgeryDetail):
    try:
        fhir_bundle = post_surgery_to_fhir_bundle(details)
        to_insert = jsonable_encoder(fhir_bundle)

        result = await patient_surgery_details.insert_one(to_insert)  # <-- await here!

        return {"inserted_id": str(result.inserted_id), "message": "Surgery details stored successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error storing surgery details: {str(e)}")

#PUT FUNCTIONS


#GET FUNCTIONS
@app.get("/getsurgerybypatient/{uhid}")
async def get_surgery_by_patient(uhid: str):
    try:
        cursor = patient_surgery_details.find({
            "entry": {
                "$elemMatch": {
                    "resource.resourceType": "Patient",
                    "resource.identifier": {
                        "$elemMatch": {"value": uhid}
                    }
                }
            }
        })

        results = []
        async for doc in cursor:
            doc["_id"] = str(doc["_id"])
            results.append(doc)

        if not results:
            raise HTTPException(status_code=404, detail=f"No surgery records found for UHID {uhid}")

        return {"patients": results}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch patients: {str(e)}")

@app.get("/patients/by-doctor-uhid/{doctor_uhid}")
async def get_patients_by_doctor_uhid(doctor_uhid: str):
    try:
        # Find patient_contact documents where doctor UHID is present
        cursor = patient_contact.find({
            "entry": {
                "$elemMatch": {
                    "resource.resourceType": "Practitioner",
                    "resource.identifier": {
                        "$elemMatch": {"value": doctor_uhid}
                    }
                }
            }
        })

        results = []
        async for doc in cursor:
            doc["_id"] = str(doc["_id"])  # Convert ObjectId to string

            # Extract UHID from Patient resource (first entry in Patient_Contact)
            patient_uhid = None
            if (
                "entry" in doc
                and len(doc["entry"]) > 0
                and doc["entry"][0]["resource"]["resourceType"] == "Patient"
            ):
                identifiers = doc["entry"][0]["resource"].get("identifier", [])
                if identifiers:
                    patient_uhid = identifiers[0].get("value")

            patient_base_doc = None
            patient_medical_doc = None

            if patient_uhid:
                # Match UHID inside patient_base first Patient resource
                patient_base_doc = await patient_base.find_one({
                    "entry.0.resource.identifier.value": patient_uhid
                })
                if patient_base_doc:
                    patient_base_doc["_id"] = str(patient_base_doc["_id"])

                # Match UHID inside patient_medical first Patient resource
                patient_medical_doc = await patient_medical.find_one({
                    "entry.0.resource.id": patient_uhid
                })
                if patient_medical_doc:
                    patient_medical_doc["_id"] = str(patient_medical_doc["_id"])

            results.append({
                "patient_contact": doc,
                "patient_base": patient_base_doc,
                "patient_medical": patient_medical_doc
            })

        if not results:
            raise HTTPException(
                status_code=404,
                detail=f"No patients found for doctor with UHID {doctor_uhid}"
            )

        return {"patients": results}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching patients: {str(e)}")

@app.get("/patients/uhid-side/{doctor_uhid}")
async def get_patient_uhid_and_side(doctor_uhid: str):
    try:
        cursor = patient_contact.find({
            "entry": {
                "$elemMatch": {
                    "resource.resourceType": "Practitioner",
                    "resource.identifier": {
                        "$elemMatch": {"value": doctor_uhid}
                    }
                }
            }
        })

        results = []
        async for doc in cursor:
            patient_uhid = None
            sides_assigned = set()
            side_scores = {}  # ✅ collect scores per side

            if "entry" in doc:
                for entry in doc["entry"]:
                    res = entry.get("resource", {})

                    # ✅ Extract patient UHID
                    if res.get("resourceType") == "Patient":
                        identifiers = res.get("identifier", [])
                        if identifiers:
                            patient_uhid = identifiers[0].get("value")

                    # ✅ Find practitioner with matching UHID and determine side(s)
                    if res.get("resourceType") == "Practitioner":
                        identifiers = res.get("identifier", [])
                        if identifiers and identifiers[0].get("value") == doctor_uhid:
                            text_div = res.get("text", {}).get("div", "")
                            if "Left" in text_div:
                                sides_assigned.add("Left")
                            if "Right" in text_div:
                                sides_assigned.add("Right")

            # ✅ If UHID found, also fetch scores for both sides
            if patient_uhid:
                if "Left" in sides_assigned:
                    patient_doc_left = await medical_left.find_one({
                        "entry.resource.resourceType": "Patient",
                        "entry.resource.text.div": {"$regex": f".*{patient_uhid}.*"}
                    })
                    if patient_doc_left:
                        scores = []
                        for entry in patient_doc_left.get("entry", []):
                            resource = entry.get("resource", {})
                            if resource.get("resourceType") == "Observation":
                                scores.append(resource)
                        side_scores["Left"] = scores

                if "Right" in sides_assigned:
                    patient_doc_right = await medical_right.find_one({
                        "entry.resource.resourceType": "Patient",
                        "entry.resource.text.div": {"$regex": f".*{patient_uhid}.*"}
                    })
                    if patient_doc_right:
                        scores = []
                        for entry in patient_doc_right.get("entry", []):
                            resource = entry.get("resource", {})
                            if resource.get("resourceType") == "Observation":
                                scores.append(resource)
                        side_scores["Right"] = scores

                results.append({
                    "patient_uhid": patient_uhid,
                    "assigned_sides": list(sides_assigned) if sides_assigned else None,
                    "scores": side_scores if side_scores else None
                })

        if not results:
            raise HTTPException(
                status_code=404,
                detail=f"No patients found for doctor with UHID {doctor_uhid}"
            )

        return {"patients": results}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching patients: {str(e)}")


#PATCH FUNCTIONS
@app.patch("/patient_surgery_details/update_field")
async def update_patient_surgery_field(update: Dict = Body(...)):
    uhid = update.get("uhid")
    field = update.get("field")
    value = update.get("value")
    period = update.get("period")  # optional, only for ROM
    component_values = update.get("component_values")  # e.g., "FEMUR", "TIBIA", "INSERT", "PATELLA"
    bone_resection_field=""
    component_field=""
    if field and "," in field:
        # For bone resection fields
        bone_resection_field, component_field = field.split(",")
    update_values = update.get("update_values")  # dictionary of fields to update
    matching_thickness = update.get("thickness")


    if not uhid:
        raise HTTPException(status_code=400, detail="uhid is required")

    patient_doc = await patient_surgery_details.find_one({
        "entry.resource.resourceType": "Patient",
        "entry.resource.identifier.value": uhid
    })

    if not patient_doc:
        raise HTTPException(status_code=404, detail="Patient not found")

    updated = False

    for i, entry in enumerate(patient_doc.get("entry", [])):
        resource = entry.get("resource", {})
        if resource.get("resourceType") != "Observation":
            continue

        code_text = resource.get("code", {}).get("text", "").lower()

        # ROM update
        if "rom" in code_text and period and value is not None:
            for k, comp in enumerate(resource.get("component", [])):
                if comp.get("code", {}).get("text") == "period" and comp.get("valueString") == period:
                    for c_idx, c in enumerate(resource.get("component", [])):
                        if c.get("code", {}).get("text") in ["flexion", "extension"]:
                            if c.get("code", {}).get("text") == update.get("field"):
                                patient_doc["entry"][i]["resource"]["component"][c_idx]["valueString"] = value
                                updated = True
                                break
                if updated:
                    break
            if updated:
                break

        # Component details update
        if component_values:
            if "components_details" in code_text and component_values:
                # Check if the requested component name matches
                if field and field.lower() not in code_text:
                    continue

                for j, comp in enumerate(resource.get("component", [])):
                    field_name = comp.get("code", {}).get("text")
                    if field_name in component_values:
                        patient_doc["entry"][i]["resource"]["component"][j]["valueString"] = component_values[field_name]
                        updated = True

                if updated:
                    break
            
        #Bone resection
        if bone_resection_field:
            if bone_resection_field in code_text:
                for j, comp in enumerate(resource.get("component", [])):
                    if comp.get("code", {}).get("text") == component_field:
                        patient_doc["entry"][i]["resource"]["component"][j]["valueString"] = value
                        updated = True
                        break

        # Only for thickness_table type
        if "thickness_table" in code_text:
            components = resource.get("component", [])
            for comp in components:
                # Match the row by thickness value
                if comp.get("code", {}).get("text") == "thickness" and comp.get("valueString") == matching_thickness:
                    # Update all fields in update_values
                    for c in components:
                        key = c.get("code", {}).get("text")
                        if key in update_values:
                            c["valueString"] = update_values[key]
                            updated = True
                    break  # Stop after updating the matching thickness row
            if updated:
                break

        # ---- General patient fields update ----
        if field and "," not in field:
            for j, comp in enumerate(resource.get("component", [])):
                if comp.get("code", {}).get("text") == field:
                    patient_doc["entry"][i]["resource"]["component"][j]["valueString"] = value
                    updated = True
                    break
            

    if not updated:
        raise HTTPException(status_code=404, detail="No matching field or component found")

    # Save updated document
    await patient_surgery_details.replace_one({"_id": patient_doc["_id"]}, patient_doc)

    return {
        "detail": "Field update success",
        "uhid": uhid,
        "updated_field": field,

    }


#DELETE FUNCTIONS


#MISCELLANEOUS ROLE
#POST FUNCTIONS
@app.post("/auth/login")
async def login(data: LoginRequest):
    identifier = data.identifier
    password = data.password
    user_type = data.type.lower()

    if user_type in ["admin", "doctor"]:
        # Check only by email + password
        user = await users_collection.find_one({
            "$and": [
                {"$or": [
                    {"uhid": identifier},
                    {"phone": identifier},
                    {"email": identifier}
                ]},
                {"password": password},
                {"type": user_type}
            ]
        })
    else:
        # Check by uhid or phone_number + password
        user = await users_collection.find_one({
            "$and": [
                {"$or": [{"uhid": identifier}, {"phone": identifier}]},
                {"password": password},
                {"type": user_type}
            ]
        })

    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    return {
        "detail": "Login successful",
        "user_id": user.get("uhid"),
        "type": user["type"]
    }

#PUT FUNCTIONS


#GET FUNCTIONS


#PATCH FUNCTIONS
@app.patch("/auth/reset-password")
async def reset_password(data: ResetPasswordRequest):
    uhid = data.uhid
    user_type = data.type.lower()
    new_password = data.new_password

    # Find and update user (case-insensitive UHID)
    result = await users_collection.update_one(
        {
            "uhid": {"$regex": f"^{uhid}$", "$options": "i"},
            "type": user_type
        },
        {"$set": {"password": new_password}}
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")

    return {"message": "Password reset successful"}


#DELETE FUNCTIONS




