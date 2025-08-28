from typing import Any, Dict, List
from fastapi import HTTPException
from motor import motor_asyncio
from fhir.resources.practitioner import Practitioner
from fhir.resources.practitionerrole import PractitionerRole
from fhir.resources.humanname import HumanName
from fhir.resources.contactpoint import ContactPoint
from fhir.resources.identifier import Identifier
from fhir.resources.bundle import Bundle, BundleEntry
from datetime import datetime
from fhir.resources.narrative import Narrative
import uuid
from datetime import datetime, timezone, date
from fhir.resources.attachment import Attachment
from pydantic import BaseModel
from models import Admin, Doctor, Feedback, PatientBase, PatientContact, PatientMedical, QuestionnaireAssignment, QuestionnaireScore

# MongoDB setup
client = motor_asyncio.AsyncIOMotorClient("mongodb+srv://admpromxp:admpromxp@promfhir.15vdylh.mongodb.net/?retryWrites=true&w=majority&appName=PromFhir")
database = client.Main
users_collection = database.Users
admin_lobby = database.Admin_Lobby 
doctor_lobby = database.Doctor_Lobby 
patient_base = database.Patient_Base
patient_contact = database.Patient_Contact
patient_medical = database.Patient_Medical
medical_left = database.Medical_Left
medical_right = database.Medical_Right
patient_surgery_details = database.Patient_Surgery_Details
feedback = database.Feedback

now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def generate_fhir_doctor_bundle(doctor: Doctor) -> dict:
    try:
        birth_date = datetime.strptime(doctor.dob, "%d-%m-%Y").date().isoformat()
    except ValueError:
        raise HTTPException(status_code=400, detail="DOB must be in 'DD-MM-YYYY' format")

    doctor_uuid = str(uuid.uuid4()).lower()
    role_uuid = str(uuid.uuid4()).lower()

    bundle = {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": []
    }

    # Practitioner resource (Doctor core info with profile photo)
    practitioner = {
        "resourceType": "Practitioner",
        "id": doctor_uuid,
        "text": {
            "status": "generated",
            "div": f"<div xmlns='http://www.w3.org/1999/xhtml'>Doctor record for {doctor.doctor_name}</div>"
        },
        "name": [{"text": doctor.doctor_name}],
        "gender": doctor.gender,
        "birthDate": birth_date,
        "telecom": [
            {"system": "email", "value": doctor.email},
            {"system": "phone", "value": doctor.phone_number}
        ],
        "identifier": [
            {
                "system": "http://hospital.org/uhid",
                "value": doctor.uhid
            }
        ]
    }

    # ✅ Add profile photo if provided
    if getattr(doctor, "profile_picture_url", None):
        practitioner["photo"] = [{
            "contentType": "image/jpeg",  # or "image/png" if applicable
            "url": doctor.profile_picture_url
        }]

    # ✅ Add Doctor Council Number if provided
    if getattr(doctor, "doctor_council_number", None):
        practitioner["identifier"].append({
            "use": "secondary",
            "type": {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/v2-0203",
                        "code": "PRN",  # Provider Number
                        "display": "Provider Number"
                    }
                ],
                "text": "Doctor Council Number"
            },
            "system": "http://hospital.smarthealth.org/doctor-council-number",
            "value": doctor.doctor_council_number,
            "assigner": {
                "display": "Medical Council Authority"
            }
        })

    # PractitionerRole resource (without CodeSystem)
    practitioner_role = {
        "resourceType": "PractitionerRole",
        "id": role_uuid,
        "text": {
            "status": "generated",
            "div": f"<div xmlns='http://www.w3.org/1999/xhtml'>Role for {doctor.doctor_name}</div>"
        },
        "practitioner": {
            "reference": f"urn:uuid:{doctor_uuid}"
        },
        "active": True,
        # ✅ Only text for designation (no CodeSystem)
        "code": [{
            "text": doctor.designation
        }],
        "identifier": [
            {
                "system": "http://hospital.org/bloodgroup",
                "value": doctor.blood_group
            },
            {
                "system": "http://hospital.org/admin-created",
                "value": doctor.admin_created
            }
        ]
    }

    # Add both resources to the bundle
    bundle["entry"].append({
        "fullUrl": f"urn:uuid:{doctor_uuid}",
        "resource": practitioner
    })
    bundle["entry"].append({
        "fullUrl": f"urn:uuid:{role_uuid}",
        "resource": practitioner_role
    })

    return bundle


def build_admin_fhir_bundle(admin: Admin) -> dict:
    dob = datetime.strptime(admin.dob, "%d-%m-%Y").date().isoformat()
    
    # Generate UUIDs for resources
    practitioner_uuid = str(uuid.uuid4())
    role_uuid = str(uuid.uuid4())

    # Practitioner resource
    practitioner = Practitioner.construct(
        id=practitioner_uuid,
        text=Narrative.construct(
            status="generated",
            div=f"<div xmlns='http://www.w3.org/1999/xhtml'>{admin.admin_name}</div>"
        ),
        name=[HumanName.construct(text=admin.admin_name)],
        gender=admin.gender,
        birthDate=dob,
        telecom=[
            ContactPoint.construct(system="email", value=admin.email),
            ContactPoint.construct(system="phone", value=admin.phone_number)
        ],
        identifier=[
            Identifier.construct(system="http://hospital.org/uhid", value=admin.uhid)
        ],
        photo=[
            Attachment.construct(
                contentType="image/jpeg",
                url=admin.profile_picture_url
            )
        ] if admin.profile_picture_url else None
    )

    # PractitionerRole resource (removed code)
    practitioner_role = PractitionerRole.construct(
        id=role_uuid,
        text=Narrative.construct(
            status="generated",
            div=f"<div xmlns='http://www.w3.org/1999/xhtml'>Hospital Administrator Role</div>"
        ),
        practitioner={"reference": f"urn:uuid:{practitioner_uuid}"},
        telecom=[ContactPoint.construct(system="email", value=admin.email)],
        active=True
    )

    # Bundle resource
    bundle = Bundle.construct(
        resourceType="Bundle",
        type="collection",
        entry=[
            BundleEntry.construct(
                fullUrl=f"urn:uuid:{practitioner_uuid}",
                resource=practitioner
            ),
            BundleEntry.construct(
                fullUrl=f"urn:uuid:{role_uuid}",
                resource=practitioner_role
            )
        ]
    )

    return bundle.dict()


# def convert_patient_to_fhir_bundle(patient: Patient) -> Dict:
#     # Create the Patient resource
#     fhir_patient = FHIRPatient(
#         id=patient.uhid,
#         identifier=[{
#             "system": "http://hospital.org/uhid",
#             "value": patient.uhid
#         }],
#         name=[HumanName(
#             family=patient.last_name,
#             given=[patient.first_name]
#         )],
#         telecom=[
#             ContactPoint(system="email", value=patient.email),
#             ContactPoint(system="phone", value=patient.phone_number),
#             ContactPoint(system="phone", value=patient.alternate_phone)
#         ],
#         gender=patient.gender,
#         birthDate=patient.dob,
#         address=[Address(text=patient.address)],
#         meta=Meta(profile=["http://hl7.org/fhir/StructureDefinition/Patient"]),
#         text=Narrative(
#             status="generated",
#             div=f"<div><p>{patient.first_name} {patient.last_name}</p></div>"
#         )
#     )

#     # Start the bundle
#     entries = [
#         BundleEntry(
#             fullUrl=f"urn:uuid:{fhir_patient.id}",
#             resource=fhir_patient
#         )
#     ]

#     # Observation: operation_funding
#     entries.append(BundleEntry(
#         fullUrl=f"urn:uuid:{uuid.uuid4()}",
#         resource=Observation(
#             id=str(uuid.uuid4()),
#             status="final",
#             code=CodeableConcept(text="Operation Funding"),
#             subject=Reference(reference=f"Patient/{patient.uhid}"),
#             valueString=patient.operation_funding
#         )
#     ))

#     # Observation: ID Proofs
#     for id_type, id_value in patient.id_proof.items():
#         entries.append(BundleEntry(
#             fullUrl=f"urn:uuid:{uuid.uuid4()}",
#             resource=Observation(
#                 id=str(uuid.uuid4()),
#                 status="final",
#                 code=CodeableConcept(text=f"ID Proof - {id_type}"),
#                 subject=Reference(reference=f"Patient/{patient.uhid}"),
#                 valueString=id_value
#             )
#         ))

#     # Observation: Activation Comments
#     for i, comment in enumerate(patient.activation_comment):
#         entries.append(BundleEntry(
#             fullUrl=f"urn:uuid:{uuid.uuid4()}",
#             resource=Observation(
#                 id=str(uuid.uuid4()),
#                 status="final",
#                 code=CodeableConcept(text="Activation Comment"),
#                 subject=Reference(reference=f"Patient/{patient.uhid}"),
#                 valueString=str(comment)
#             )
#         ))

#     # Observation: Admin Name
#     if patient.admin_name:
#         entries.append(BundleEntry(
#             fullUrl=f"urn:uuid:{uuid.uuid4()}",
#             resource=Observation(
#                 id=str(uuid.uuid4()),
#                 status="final",
#                 code=CodeableConcept(text="Admin Name"),
#                 subject=Reference(reference=f"Patient/{patient.uhid}"),
#                 valueString=patient.admin_name
#             )
#         ))

#     # Observation: Doctor Name
#     if patient.doctor_name:
#         entries.append(BundleEntry(
#             fullUrl=f"urn:uuid:{uuid.uuid4()}",
#             resource=Observation(
#                 id=str(uuid.uuid4()),
#                 status="final",
#                 code=CodeableConcept(text="Doctor Name"),
#                 subject=Reference(reference=f"Patient/{patient.uhid}"),
#                 valueString=patient.doctor_name
#             )
#         ))

#     # Final Bundle
#     bundle = Bundle(
#         id=str(uuid.uuid4()),
#         type="document",
#         timestamp=datetime.utcnow().isoformat(),
#         entry=entries
#     )

#     # Fix: convert date to string-safe format
#     return bundle.model_dump(mode="json")

# def generate_full_url(resource_type: str, resource_id: str) -> str:
#     return f"urn:uuid:{resource_id}"

# def convert_patient_to_fhir(patient) -> Dict[str, Any]:
#     patient_id = str(uuid.uuid4())
#     patient_full_url = generate_full_url("Patient", patient_id)

#     # Practitioner Resource
#     practitioner_id = str(uuid.uuid4())
#     practitioner_full_url = generate_full_url("Practitioner", practitioner_id)
#     practitioner_resource = {
#         "fullUrl": practitioner_full_url,
#         "resource": {
#             "resourceType": "Practitioner",
#             "id": practitioner_id,
#             "name": [{"text": "Default Practitioner"}],
#             "text": {
#                 "status": "generated",
#                 "div": "<div xmlns='http://www.w3.org/1999/xhtml'>Practitioner: Default Practitioner</div>"
#             }
#         }
#     }

#     # Extract readable values
#     funding = getattr(patient, 'operationfundion', '')
#     comments = getattr(patient, 'activation_comment', [])
#     comments_str = " | ".join(c.get("comment", "") for c in comments if isinstance(c, dict))

#     # Patient Resource
#     patient_resource = {
#         "resourceType": "Patient",
#         "id": patient_id,
#         "text": {
#             "status": "generated",
#             "div": f"<div xmlns='http://www.w3.org/1999/xhtml'>"
#                    f"Patient: {patient.first_name or ''} {patient.last_name or ''} - "
#                    f"Funding: {funding} - "
#                    f"Comments: {comments_str}"
#                    f"</div>"
#         },
#         "active": getattr(patient, 'activation_status', True),
#         "identifier": [
#             {
#                 "use": "usual",
#                 "type": {
#                     "coding": [
#                         {
#                             "system": "http://terminology.hl7.org/CodeSystem/v2-0203",
#                             "code": "MR",
#                             "display": "Medical Record Number"
#                         }
#                     ]
#                 },
#                 "value": patient.uhid
#             }
#         ],
#         "name": [
#             {
#                 "use": "official",
#                 "family": patient.last_name or '',
#                 "given": [patient.first_name or '']
#             }
#         ],
#         "telecom": [],
#         "gender": patient.gender,
#         "birthDate": patient.dob,
#         "address": [{"text": patient.address}],
#         "contact": [],
#         "communication": [
#             {
#                 "language": {
#                     "coding": [
#                         {
#                             "system": "urn:ietf:bcp:47",
#                             "code": "en",
#                             "display": "English"
#                         }
#                     ]
#                 },
#                 "preferred": True
#             }
#         ]
#     }

#     # Telecom
#     if getattr(patient, 'phone_number', None):
#         patient_resource["telecom"].append({
#             "system": "phone", "value": patient.phone_number, "use": "mobile"
#         })
#     if getattr(patient, 'alternatenumber', None):
#         patient_resource["telecom"].append({
#             "system": "phone", "value": patient.alternatenumber, "use": "home"
#         })
#     if getattr(patient, 'email', None):
#         patient_resource["telecom"].append({
#             "system": "email", "value": patient.email, "use": "home"
#         })

#     # Contacts
#     if getattr(patient, 'admin_uhid', None):
#         patient_resource["contact"].append({
#             "name": {"text": "Admin"},
#             "telecom": [{"system": "other", "value": patient.admin_uhid}],
#             "relationship": [
#                 {
#                     "coding": [
#                         {
#                             "system": "http://terminology.hl7.org/CodeSystem/v2-0131",
#                             "code": "C",
#                             "display": "Emergency Contact"
#                         }
#                     ]
#                 }
#             ]
#         })

#     if getattr(patient, 'doctor_uhid', None):
#         patient_resource["contact"].append({
#             "name": {"text": "Doctor"},
#             "telecom": [{"system": "other", "value": patient.doctor_uhid}],
#             "relationship": [
#                 {
#                     "coding": [
#                         {
#                             "system": "http://terminology.hl7.org/CodeSystem/v2-0131",
#                             "code": "PR",
#                             "display": "Person preparing referral"
#                         }
#                     ]
#                 }
#             ]
#         })

#     # ID Proof Identifier
#     idproof = getattr(patient, 'idproof', None)
#     if isinstance(idproof, dict):
#         idproof_value = idproof.get("value")
#         idproof_type = idproof.get("type", "unknown")
#         if idproof_value:
#             patient_resource["identifier"].append({
#                 "use": "official",
#                 "type": {
#                     "coding": [
#                         {
#                             "system": "http://hospital.org/idproof",
#                             "code": idproof_type,
#                             "display": idproof_type.title()
#                         }
#                     ],
#                     "text": idproof_type.title()
#                 },
#                 "value": idproof_value
#             })

#     effective_dt = getattr(patient, 'opd_appointment_date', None)
#     if not effective_dt:
#         effective_dt = datetime.utcnow().date().isoformat()

#     # Replace your existing create_observation with this
#     def create_observation(display, text_display, quantity, value_type="valueQuantity"):
#         obs_id = str(uuid.uuid4())
#         return {
#             "fullUrl": generate_full_url("Observation", obs_id),
#             "resource": {
#                 "resourceType": "Observation",
#                 "id": obs_id,
#                 "status": "final",
#                 "category": [
#                     {
#                         "coding": [
#                             {
#                                 "system": "http://terminology.hl7.org/CodeSystem/observation-category",
#                                 "code": "vital-signs",
#                                 "display": "Vital Signs"
#                             }
#                         ]
#                     }
#                 ],
#                 "code": {
#                     "text": text_display
#                 },
#                 "subject": {"reference": patient_full_url},
#                 "effectiveDateTime": effective_dt,
#                 "performer": [{"reference": practitioner_full_url}],
#                 value_type: quantity,
#                 "text": {
#                     "status": "generated",
#                     "div": f"<div xmlns='http://www.w3.org/1999/xhtml'>{display}: {quantity.get('value', '')} {quantity.get('unit', '')}</div>"
#                 }
#             }
#         }


#     # Blood Group Observation
#     blood_obs_id = str(uuid.uuid4())
#     blood_group_observation = {
#         "fullUrl": generate_full_url("Observation", blood_obs_id),
#         "resource": {
#             "resourceType": "Observation",
#             "id": blood_obs_id,
#             "status": "final",
#             "category": [
#                 {
#                     "coding": [
#                         {
#                             "system": "http://terminology.hl7.org/CodeSystem/observation-category",
#                             "code": "laboratory",
#                             "display": "Laboratory"
#                         }
#                     ]
#                 }
#             ],
#             "code": {
#                 "coding": [
#                     {
#                         "system": "http://loinc.org",
#                         "code": "883-9",
#                         "display": "ABO group [Type] in Blood"
#                     }
#                 ],
#                 "text": "Blood Group"
#             },
#             "subject": {"reference": patient_full_url},
#             "effectiveDateTime": patient.dob,
#             "performer": [{"reference": practitioner_full_url}],
#             "valueString": patient.blood_grp,
#             "text": {
#                 "status": "generated",
#                 "div": f"<div xmlns='http://www.w3.org/1999/xhtml'>Blood Group: {patient.blood_grp}</div>"
#             }
#         }
#     }

#     if patient.height is not None:
#         height_obs = create_observation(
#             "Height",
#             "Height (cm)",
#             {
#                 "value": patient.height,
#                 "unit": "cm",
#                 "system": "http://unitsofmeasure.org",
#                 "code": "cm"
#             }
#         )

#     if patient.weight is not None:
#         weight_obs = create_observation(
#             "Weight",
#             "Weight (kg)",
#             {
#                 "value": patient.weight,
#                 "unit": "kg",
#                 "system": "http://unitsofmeasure.org",
#                 "code": "kg"
#             }
#         )


#     # VIP Flag
#     vip_flag = None
#     if getattr(patient, 'vip', 0) == 1:
#         vip_id = str(uuid.uuid4())
#         vip_flag = {
#             "fullUrl": generate_full_url("Flag", vip_id),
#             "resource": {
#                 "resourceType": "Flag",
#                 "id": vip_id,
#                 "status": "active",
#                 "code": {"text": "VIP Patient"},
#                 "subject": {"reference": patient_full_url},
#                 "text": {
#                     "status": "generated",
#                     "div": "<div xmlns='http://www.w3.org/1999/xhtml'>VIP Patient</div>"
#                 }
#             }
#         }

#     # Final Bundle
#     entries = [
#         {"fullUrl": patient_full_url, "resource": patient_resource},
#         practitioner_resource,
#         blood_group_observation,
#     ]

#     if height_obs:
#         entries.append(height_obs)
#     if weight_obs:
#         entries.append(weight_obs)
#     if vip_flag:
#         entries.append(vip_flag)

#     return {
#         "resourceType": "Bundle",
#         "type": "collection",
#         "entry": entries
#     }

def convert_patientbase_to_fhir(patient) -> dict:
    patient_uuid = str(uuid.uuid4())
    vip_obs_uuid = str(uuid.uuid4())

    # ✅ Ensure date is in YYYY-MM-DD format
    try:
        birth_date = datetime.strptime(patient.dob, "%d-%m-%Y").strftime("%Y-%m-%d")
    except ValueError:
        birth_date = patient.dob  # Assume already in correct format

    # Patient resource
    fhir_patient = {
        "resourceType": "Patient",
        "id": patient_uuid,
        "identifier": [
            {
                "use": "usual",
                "type": {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/v2-0203",
                            "code": "MR",
                            "display": "Medical Record Number"
                        }
                    ],
                    "text": "UHID"
                },
                "system": "http://hospital.smarthealth.org/uhid",
                "value": patient.uhid
            }
        ],
        "name": [
            {
                "use": "official",
                "family": patient.last_name,
                "given": [patient.first_name]
            }
        ],
        "gender": patient.gender,
        "birthDate": birth_date,
        "meta": {
            "profile": [
                "http://hl7.org/fhir/StructureDefinition/Patient"
            ]
        },
        "text": {
            "status": "generated",
            "div": f"<div xmlns='http://www.w3.org/1999/xhtml'>Patient: {patient.first_name} {patient.last_name}</div>"
        }
    }

    # Observation resource (VIP status)
    fhir_vip_observation = {
        "resourceType": "Observation",
        "id": vip_obs_uuid,
        "status": "final",
        "category": [
            {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                        "code": "social-history",
                        "display": "Social History"
                    }
                ]
            }
        ],
        "code": {
            "text": "VIP Status"
        },
        "subject": {
            "reference": f"urn:uuid:{patient_uuid}"
        },
        "effectiveDateTime": datetime.now(timezone.utc).isoformat(),
        "valueBoolean": bool(patient.vip),
        "performer": [
            {
                "display": "Hospital Staff"
            }
        ],
        "text": {
            "status": "generated",
            "div": f"<div xmlns='http://www.w3.org/1999/xhtml'>VIP Status: {'Yes' if patient.vip else 'No'}</div>"
        }
    }

    # Bundle resource
    bundle = {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [
            {
                "fullUrl": f"urn:uuid:{patient_uuid}",
                "resource": fhir_patient
            },
            {
                "fullUrl": f"urn:uuid:{vip_obs_uuid}",
                "resource": fhir_vip_observation
            }
        ]
    }

    return bundle


def convert_to_patientcontact_fhir_bundle(contact: PatientContact) -> dict:
    from datetime import datetime, timedelta
    import uuid

    def generate_id():
        return f"urn:uuid:{uuid.uuid4()}"

    def narrative(text):
        return {
            "status": "generated",
            "div": f"<div xmlns='http://www.w3.org/1999/xhtml'>{text}</div>"
        }

    now = datetime.utcnow()
    start_time = now.isoformat() + "Z"
    end_time = (now + timedelta(minutes=30)).isoformat() + "Z"

    # Generate full URLs
    patient_id = generate_id()
    doctor_left_id = generate_id()
    doctor_right_id = generate_id()
    admin_id = generate_id()
    appointment_id = generate_id()

    bundle = {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [
            {
                "fullUrl": patient_id,
                "resource": {
                    "resourceType": "Patient",
                    "id": patient_id.split(":")[-1],
                    "text": narrative("Patient resource"),
                    "identifier": [{
                        "use": "usual",
                        "type": {
                            "coding": [{
                                "system": "http://terminology.hl7.org/CodeSystem/v2-0203",
                                "code": "MR",
                                "display": "Medical Record Number"
                            }]
                        },
                        "system": "http://hospital.smarthealth.org/uhid",
                        "value": contact.uhid
                    }],
                    "telecom": [
                        {"system": "phone", "value": contact.phone_number, "use": "mobile"},
                        {"system": "phone", "value": contact.alternate_phone, "use": "home"},
                        {"system": "email", "value": contact.email, "use": "home"}
                    ],
                    "address": [{"text": contact.address}],
                    "photo": [{"url": contact.profile_picture_url}] if contact.profile_picture_url else []
                }
            },
            {
                "fullUrl": doctor_left_id,
                "resource": {
                    "resourceType": "Practitioner",
                    "id": doctor_left_id.split(":")[-1],
                    "text": narrative("Left Doctor"),
                    "identifier": [{
                        "system": "http://hospital.smarthealth.org/uhid",
                        "value": contact.doctor_uhid_left
                    }]
                }
            },
            {
                "fullUrl": doctor_right_id,
                "resource": {
                    "resourceType": "Practitioner",
                    "id": doctor_right_id.split(":")[-1],
                    "text": narrative("Right Doctor"),
                    "identifier": [{
                        "system": "http://hospital.smarthealth.org/uhid",
                        "value": contact.doctor_uhid_right
                    }]
                }
            },
            {
                "fullUrl": admin_id,
                "resource": {
                    "resourceType": "Practitioner",
                    "id": admin_id.split(":")[-1],
                    "text": narrative("Admin Staff"),
                    "identifier": [{
                        "system": "http://hospital.smarthealth.org/uhid",
                        "value": contact.admin_uhid
                    }]
                }
            },
            {
                "fullUrl": appointment_id,
                "resource": {
                    "resourceType": "Appointment",
                    "id": appointment_id.split(":")[-1],
                    "text": narrative("OPD Appointment"),
                    "status": "booked",
                    "start": start_time,
                    "end": end_time,
                    "participant": [
                        {"actor": {"reference": patient_id}, "status": "accepted"},
                        {"actor": {"reference": doctor_left_id}, "status": "accepted"},
                        {"actor": {"reference": doctor_right_id}, "status": "accepted"},
                        {"actor": {"reference": admin_id}, "status": "accepted"}
                    ]
                }
            }
        ]
    }

    return bundle


def convert_patientmedical_to_fhir(patient) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    subject_ref = f"urn:uuid:{str(uuid.uuid4())}"

    entries = []

    # Patient Resource
    entries.append({
        "fullUrl": subject_ref,
        "resource": {
            "resourceType": "Patient",
            "id": patient.uhid,
            "text": {
                "status": "generated",
                "div": f"<div xmlns='http://www.w3.org/1999/xhtml'>Patient UHID: {patient.uhid}</div>"
            }
        }
    })

    def create_observation(code_text, value, value_type="valueString", unit=None):
        obs = {
            "resourceType": "Observation",
            "id": str(uuid.uuid4()),
            "status": "final",
            "code": {"text": code_text},
            "subject": {"reference": subject_ref},
            "effectiveDateTime": now,
            "performer": [{"reference": subject_ref}],
            "text": {
                "status": "generated",
                "div": f"<div xmlns='http://www.w3.org/1999/xhtml'>{code_text}: {value} {unit or ''}</div>"
            }
        }
        # Set value type
        if value_type == "valueQuantity" and unit:
            obs["valueQuantity"] = {"value": value, "unit": unit}
        elif value_type == "valueBoolean":
            obs["valueBoolean"] = value
        else:
            obs["valueString"] = str(value)
        return {"fullUrl": f"urn:uuid:{str(uuid.uuid4())}", "resource": obs}

    # Core patient observations
    entries.append(create_observation("Blood Group", patient.blood_grp))
    entries.append(create_observation("Height", patient.height, value_type="valueQuantity", unit="cm"))
    entries.append(create_observation("Weight", patient.weight, value_type="valueQuantity", unit="kg"))

    # Activation Status
    entries.append(create_observation("Activation Status", patient.activation_status, value_type="valueBoolean"))

    # Activation Comments → Provenance
    for comment in patient.activation_comment:
        comment_time = comment.timestamp if hasattr(comment, 'timestamp') else now
        if 'T' not in comment_time:
            comment_time += "T00:00:00Z"
        entries.append({
            "fullUrl": f"urn:uuid:{str(uuid.uuid4())}",
            "resource": {
                "resourceType": "Provenance",
                "id": str(uuid.uuid4()),
                "target": [{"reference": subject_ref}],
                "recorded": comment_time,
                "activity": {"text": "Activation Comment"},
                "agent": [{"type": {"text": "Practitioner"}, "who": {"reference": subject_ref}}],
                "text": {
                    "status": "generated",
                    "div": f"<div xmlns='http://www.w3.org/1999/xhtml'>Comment: {comment.comment}</div>"
                }
            }
        })

    # Patient Follow-up Comments → Provenance
    for comment in patient.patient_followup_comment:
        comment_time = comment.timestamp if hasattr(comment, 'timestamp') else now
        if 'T' not in comment_time:
            comment_time += "T00:00:00Z"
        entries.append({
            "fullUrl": f"urn:uuid:{str(uuid.uuid4())}",
            "resource": {
                "resourceType": "Provenance",
                "id": str(uuid.uuid4()),
                "target": [{"reference": subject_ref}],
                "recorded": comment_time,
                "activity": {"text": "Patient Follow-up Comment"},
                "agent": [{"type": {"text": "Practitioner"}, "who": {"reference": subject_ref}}],
                "text": {
                    "status": "generated",
                    "div": f"<div xmlns='http://www.w3.org/1999/xhtml'>Follow-up Comment: {comment.comment}</div>"
                }
            }
        })

    # Surgery Status Observations
    entries.append(create_observation("Patient Current Status", patient.patient_current_status))
    if patient.surgery_date_left:
        entries.append(create_observation("Surgery Date Left", patient.surgery_date_left))
    if patient.surgery_date_right:
        entries.append(create_observation("Surgery Date Right", patient.surgery_date_right))

    # Organization resource for operation funding
    org_uuid = str(uuid.uuid4())
    org_ref = f"urn:uuid:{org_uuid}"
    entries.append({
        "fullUrl": org_ref,
        "resource": {
            "resourceType": "Organization",
            "id": org_uuid,
            "name": patient.operation_funding,
            "text": {
                "status": "generated",
                "div": f"<div xmlns='http://www.w3.org/1999/xhtml'>Organization: {patient.operation_funding}</div>"
            }
        }
    })

    # Coverage: Operation Funding
    entries.append({
        "fullUrl": f"urn:uuid:{str(uuid.uuid4())}",
        "resource": {
            "resourceType": "Coverage",
            "id": str(uuid.uuid4()),
            "status": "active",
            "kind": "insurance",
            "type": {"text": patient.operation_funding},
            "beneficiary": {"reference": subject_ref},
            "subscriber": {"reference": subject_ref},
            "text": {
                "status": "generated",
                "div": f"<div xmlns='http://www.w3.org/1999/xhtml'>Funding Source: {patient.operation_funding}</div>"
            }
        }
    })

    # ID Proofs
    for id_type, id_value in patient.id_proof.items():
        entries.append({
            "fullUrl": f"urn:uuid:{str(uuid.uuid4())}",
            "resource": {
                "resourceType": "DocumentReference",
                "id": str(uuid.uuid4()),
                "status": "current",
                "type": {"text": id_type.upper()},
                "subject": {"reference": subject_ref},
                "content": [{"attachment": {"title": id_value, "url": f"urn:idproof:{id_type}:{id_value}"}}],
                "text": {
                    "status": "generated",
                    "div": f"<div xmlns='http://www.w3.org/1999/xhtml'>{id_type.upper()}: {id_value}</div>"
                }
            }
        })

    return {
        "resourceType": "Bundle",
        "id": str(uuid.uuid4()),
        "type": "collection",
        "timestamp": now,
        "entry": entries
    }

# Utility: choose collection based on side
LOINC_CODE_MAP = {
    "Oxford Knee Score (OKS)": ("unknown", "Unknown"),
    "Short Form - 12 (SF-12)": ("unknown", "Unknown"),
    "Knee Society Score (KSS)": ("unknown", "Unknown"),
    "Knee Injury and Osteoarthritis Outcome Score, Joint Replacement (KOOS, JR)": ("unknown", "Unknown"),
    "Forgotten Joint Score (FJS)": ("unknown", "Unknown")
}

def get_collection(side: str):
    return medical_left if side == "left" else medical_right

def generate_fhir_bundle(assignments: List[QuestionnaireAssignment], scores: List[QuestionnaireScore] = None, existing_patient_uuid: str = None, patient_id: str = None):
    bundle = {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": []
    }

    if assignments:
        patient_uuid = existing_patient_uuid or str(uuid.uuid4())
        patient_id = patient_id or assignments[0].uhid.lower()

        if not existing_patient_uuid:
            bundle["entry"].append({
                "fullUrl": f"urn:uuid:{patient_uuid}",
                "resource": {
                    "resourceType": "Patient",
                    "id": patient_uuid,
                    "text": {
                        "status": "generated",
                        "div": f'<div xmlns="http://www.w3.org/1999/xhtml"><p>Patient ID: {patient_id}</p></div>'
                    }
                }
            })

        for a in assignments:
            obs_uuid = str(uuid.uuid4())
            code, display = LOINC_CODE_MAP.get(a.name, ("unknown", "Unknown"))

            # Match score for the same UHID, side, and name
            matching_score = None
            if scores:
                matching_score = next((s for s in scores if s.uhid == a.uhid and s.side == a.side and s.name == a.name and s.period == a.period), None)

            observation = {
                "resourceType": "Observation",
                "id": obs_uuid,
                "status": "preliminary",
                "subject": {
                    "reference": f"urn:uuid:{patient_uuid}"
                },
                "performer": [
                    {
                        "display": "Automated system"
                    }
                ],
                "code": {
                    "coding": [{
                        "system": "http://terminology.hl7.org/CodeSystem/data-absent-reason",
                        "code": code,
                        "display": display
                    }],
                    "text": a.name
                },
                "effectivePeriod": {
                    "start": a.assigned_date,
                    "end": a.deadline
                },
                "component": [
                    {
                        "code": {
                            "text": "Completion Status"
                        },
                        "valueBoolean": bool(a.completed)
                    }
                ],
                "valueString": f"Scores ({a.period})",
                "text": {
                    "status": "generated",
                    "div": f'<div xmlns="http://www.w3.org/1999/xhtml"><p>{a.name} Scores ({a.period}), Completed: {a.completed}</p></div>'
                }
            }

            # Add scores as components
            if matching_score and matching_score.score:
                for idx, val in enumerate(matching_score.score, start=1):
                    observation["component"].append({
                        "code": {
                            "text": f"Score {idx}"
                        },
                        "valueInteger": val
                    })

            # Add others as notes
            if matching_score and matching_score.others:
                observation["note"] = [{"text": note} for note in matching_score.others]

            bundle["entry"].append({
                "fullUrl": f"urn:uuid:{obs_uuid}",
                "resource": observation
            })

    return bundle



def post_surgery_to_fhir_bundle(post_surgery_detail: BaseModel) -> Dict[str, Any]:
    def observation_from_data(
        subject_ref: str,
        code_text: str,
        data: Any,
        effectiveDateTime: str = None,
    ) -> List[Dict[str, Any]]:
        observations = []

        def next_id():
            return str(uuid.uuid4()).lower()  # UUID lowercase, no prefix here to satisfy validator

        if isinstance(data, dict):
            components = []
            for key, value in data.items():
                if isinstance(value, dict) or isinstance(value, list):
                    observations.extend(observation_from_data(subject_ref, f"{code_text} - {key}", value, effectiveDateTime))
                else:
                    components.append({
                        "code": {"text": key},
                        "valueString": str(value) if value is not None else ""
                    })
            if components:
                obs_id = next_id()
                obs = {
                    "resourceType": "Observation",
                    "id": obs_id,
                    "status": "final",
                    "category": [{
                        "coding": [{
                            "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                            "code": "exam",
                            "display": "Exam"
                        }]
                    }],
                    "code": {"text": code_text},
                    "subject": {"reference": subject_ref},
                    "performer": [
                        {
                            "reference": org_fullUrl,
                            "display": "Hospital Organization"
                        }
                    ],
                    "component": components,
                    "text": {
                        "status": "generated",
                        "div": f'<div xmlns="http://www.w3.org/1999/xhtml">{code_text} Observation with components</div>'
                    },
                }
                if effectiveDateTime:
                    obs["effectiveDateTime"] = effectiveDateTime

                observations.append(obs)

        elif isinstance(data, list):
            for i, item in enumerate(data):
                obs_text = f"{code_text} [{i+1}]"
                observations.extend(observation_from_data(subject_ref, obs_text, item, effectiveDateTime))

        else:
            obs_id = next_id()
            obs = {
                "resourceType": "Observation",
                "id": obs_id,
                "status": "final",
                "category": [{
                    "coding": [{
                        "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                        "code": "exam",
                        "display": "Exam"
                    }]
                }],
                "code": {"text": code_text},
                "subject": {"reference": subject_ref},
                "performer": [
                    {
                        "reference": org_fullUrl,
                        "display": "Hospital Organization"
                    }
                ],
                "valueString": str(data) if data is not None else "",
                "text": {
                    "status": "generated",
                    "div": f'<div xmlns="http://www.w3.org/1999/xhtml">{code_text} Observation</div>'
                },
            }
            if effectiveDateTime:
                obs["effectiveDateTime"] = effectiveDateTime

            observations.append(obs)

        return observations

    bundle = {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": []
    }

    # Generate UUIDs for Patient and Organization (lowercase)
    patient_uuid = str(uuid.uuid4()).lower()
    org_uuid = str(uuid.uuid4()).lower()

    # Use original uhid as identifier only, not resource ID
    uhid = getattr(post_surgery_detail, "uhid", "unknown-patient").lower()

    patient_resource = {
        "resourceType": "Patient",
        "id": patient_uuid,
        "identifier": [{"system": "urn:your-hospital-system", "value": uhid}],
        "text": {
            "status": "generated",
            "div": f'<div xmlns="http://www.w3.org/1999/xhtml">Patient ID: {uhid}</div>'
        }
    }
    patient_fullUrl = f"urn:uuid:{patient_uuid}"
    bundle["entry"].append({"fullUrl": patient_fullUrl, "resource": patient_resource})

    org_resource = {
        "resourceType": "Organization",
        "id": org_uuid,
        "name": "Hospital Organization",
        "text": {
            "status": "generated",
            "div": '<div xmlns="http://www.w3.org/1999/xhtml">Hospital Organization</div>'
        }
    }
    org_fullUrl = f"urn:uuid:{org_uuid}"
    bundle["entry"].append({"fullUrl": org_fullUrl, "resource": org_resource})

    subject_ref = patient_fullUrl

    data_dict = post_surgery_detail.dict()
    data_dict.pop("uhid", None)

    first_posting_timestamp = None
    patient_records = data_dict.get("patient_records", [])
    if patient_records and isinstance(patient_records, list):
        first_posting_timestamp = patient_records[0].get("posting_timestamp")

    observations = observation_from_data(subject_ref, "PostSurgeryDetail", data_dict, effectiveDateTime=first_posting_timestamp)

    for obs in observations:
        obs_fullUrl = f"urn:uuid:{obs['id']}"
        bundle["entry"].append({"fullUrl": obs_fullUrl, "resource": obs})

    return bundle

def feedback_to_fhir_bundle(feedback: Feedback) -> Dict:
    observation_id = str(uuid.uuid4())

    # Create Observation resource without invalid LOINC and example URLs
    observation_resource = {
        "resourceType": "Observation",
        "id": observation_id,
        "status": "final",
        "category": [
            {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                        "code": "survey",
                        "display": "Survey"
                    }
                ]
            }
        ],
        "code": {
            "text": f"Patient Feedback for {feedback.period}"
        },
        "subject": {
            "identifier": {
                "system": "urn:uhid",  # Avoid example.org
                "value": feedback.uhid
            }
        },
        "effectiveDateTime": feedback.timestamp.isoformat(),
        "performer": [
            {
                "display": "Patient"
            }
        ],
        "component": [],
        "text": {
            "status": "generated",
            "div": f"<div xmlns='http://www.w3.org/1999/xhtml'><p>Feedback for {feedback.period}, Side: {feedback.side}</p></div>"
        }
    }

    # Add period as component
    observation_resource["component"].append({
        "code": {
            "text": "Feedback Period"
        },
        "valueString": feedback.period
    })

    # Add side as component
    observation_resource["component"].append({
        "code": {
            "text": "Affected Side"
        },
        "valueString": feedback.side
    })

    # Add ratings as components
    for idx, r in enumerate(feedback.rating, start=1):
        observation_resource["component"].append({
            "code": {
                "text": f"Rating {idx}"
            },
            "valueInteger": r
        })

    # Create FHIR Bundle
    bundle = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            {
                "fullUrl": f"urn:uuid:{observation_id}",
                "resource": observation_resource,
                "request": {
                    "method": "POST",
                    "url": "Observation"
                }
            }
        ]
    }

    return bundle