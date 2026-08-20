from sqlalchemy import (
    Column, Integer, String, Boolean, Numeric, ForeignKey, Text, TIMESTAMP, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from .database import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False)  # 'doctor' | 'secretary'
    full_name = Column(String)
    is_active = Column(Boolean, default=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())


class Patient(Base):
    __tablename__ = "patients"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    phone = Column(String, unique=True, nullable=False)
    age = Column(Integer)
    gender = Column(String)
    whatsapp_chat_id = Column(String)
    whatsapp_opt_in = Column(Boolean, default=True)
    last_reengagement_sent_at = Column(TIMESTAMP(timezone=True))
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())


class Doctor(Base):
    __tablename__ = "doctors"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    specialty = Column(String)
    is_active = Column(Boolean, default=True)
    user_id = Column(Integer, ForeignKey("users.id"))


class Appointment(Base):
    __tablename__ = "appointments"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("patients.id"))
    doctor_id = Column(Integer, ForeignKey("doctors.id"))
    scheduled_at = Column(TIMESTAMP(timezone=True), nullable=False)
    status = Column(String, default="confirmed")  # confirmed | cancelled | done | no_show
    appointment_type = Column(String, nullable=False, default="consultation")  # consultation | treatment
    treatment_item_id = Column(Integer, ForeignKey("treatment_items.id"))
    confirmation_sent = Column(Boolean, default=False)
    reminder_sent = Column(Boolean, default=False)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    patient = relationship("Patient")
    doctor = relationship("Doctor")
    treatment_item = relationship("TreatmentItem")


class Procedure(Base):
    __tablename__ = "procedures"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    base_price = Column(Numeric(10, 2), nullable=False)
    unit = Column(String, nullable=False)


class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("patients.id"))
    appointment_id = Column(Integer, ForeignKey("appointments.id"))
    treatment_item_id = Column(Integer, ForeignKey("treatment_items.id"))
    base_amount = Column(Numeric(10, 2), nullable=False)
    discount_amount = Column(Numeric(10, 2), default=0)
    insurance_covered_amount = Column(Numeric(10, 2), default=0)
    final_amount = Column(Numeric(10, 2), nullable=False)
    method = Column(String, default="cash")
    receipt_path = Column(String)  # relative to app/uploads/, e.g. "receipts/17_a1b2c3d4e5.jpg"; null for cash
    status = Column(String, default="pending")
    posted_by_doctor_id = Column(Integer, ForeignKey("users.id"))
    collected_by_secretary_id = Column(Integer, ForeignKey("users.id"))
    notes = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    paid_at = Column(TIMESTAMP(timezone=True))

    patient = relationship("Patient")
    appointment = relationship("Appointment")
    treatment_item = relationship("TreatmentItem")
    procedures = relationship("PatientProcedure", back_populates="payment")


class PatientProcedure(Base):
    __tablename__ = "patient_procedures"
    id = Column(Integer, primary_key=True)
    payment_id = Column(Integer, ForeignKey("payments.id"))
    patient_id = Column(Integer, ForeignKey("patients.id"))
    procedure_id = Column(Integer, ForeignKey("procedures.id"))
    quantity = Column(Integer, default=1)
    price_charged = Column(Numeric(10, 2))
    tooth_area = Column(String)
    performed_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    payment = relationship("Payment", back_populates="procedures")
    procedure = relationship("Procedure")


class TreatmentItem(Base):
    """One line of a patient's persistent treatment plan -- created from the
    doctor's post-consultation voice note (see voice_treatment_plan.py),
    booked against later by the WhatsApp agent (get_pending_treatments /
    book_appointment), and closed out when the secretary marks its linked
    appointment done."""
    __tablename__ = "treatment_items"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    procedure_id = Column(Integer, ForeignKey("procedures.id"), nullable=False)
    tooth_area = Column(String)
    status = Column(String, nullable=False, default="pending")  # pending | in_progress | completed
    notes = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    completed_at = Column(TIMESTAMP(timezone=True))

    patient = relationship("Patient")
    procedure = relationship("Procedure")


class PaymentAdjustment(Base):
    __tablename__ = "payment_adjustments"
    id = Column(Integer, primary_key=True)
    payment_id = Column(Integer, ForeignKey("payments.id"))
    adjusted_by = Column(Integer, ForeignKey("users.id"))
    adjustment_type = Column(String, nullable=False)
    old_amount = Column(Numeric(10, 2))
    new_amount = Column(Numeric(10, 2))
    reason = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())


class AnalyticsSnapshot(Base):
    """One cached copy of a CLOSED month's analytics (the full dict
    build_monthly_analytics returns), so past months don't re-run an LLM call
    on every dashboard view. The current month is always computed fresh and
    never stored here — see doctor.py's /analytics/monthly."""
    __tablename__ = "analytics_snapshots"
    __table_args__ = (UniqueConstraint("year", "month", name="uq_analytics_snapshot_month"),)
    id = Column(Integer, primary_key=True)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    snapshot = Column(JSONB, nullable=False)
    insights = Column(Text)
    generated_at = Column(TIMESTAMP(timezone=True), server_default=func.now())