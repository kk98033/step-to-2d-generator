"""Additive schema for explicitly curated native-feature tolerance libraries."""
from types import SimpleNamespace
from sqlalchemy import Table, Column, String, Text, Boolean, DateTime, ForeignKey, UniqueConstraint


def define_case_library_tables(metadata):
    customers = Table('library_private_customers', metadata,
        Column('id', String(36), primary_key=True),
        Column('owner_user_id', String(36), ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        Column('name', String(160), nullable=False),
        Column('created_at', DateTime(timezone=True), nullable=False),
        UniqueConstraint('owner_user_id', 'name', name='uq_private_customer_owner_name'))
    collections = Table('library_collections', metadata,
        Column('id', String(36), primary_key=True),
        Column('owner_user_id', String(36), ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        Column('name', String(160), nullable=False),
        Column('description', Text, nullable=False, default=''),
        Column('private_customer_id', String(36), ForeignKey('library_private_customers.id'), nullable=True),
        Column('customer_tag_id', String(36), ForeignKey('recommendation_tags.id'), nullable=True),
        Column('is_active', Boolean, nullable=False, default=True),
        Column('created_at', DateTime(timezone=True), nullable=False),
        Column('updated_at', DateTime(timezone=True), nullable=False))
    payloads = Table('library_case_payloads', metadata,
        Column('case_record_id', String(36), primary_key=True),
        Column('owner_user_id', String(36), ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        Column('artifact_id', String(36), ForeignKey('annotation_artifacts.id'), nullable=False),
        Column('case_id', String(160), nullable=False, unique=True),
        Column('content_hash', String(64), nullable=False),
        Column('case_json', Text, nullable=False),
        Column('is_active', Boolean, nullable=False, default=True),
        Column('created_at', DateTime(timezone=True), nullable=False),
        UniqueConstraint('owner_user_id', 'content_hash', name='uq_native_case_owner_content'))
    memberships = Table('library_collection_cases', metadata,
        Column('collection_id', String(36), ForeignKey('library_collections.id', ondelete='CASCADE'), primary_key=True),
        Column('case_record_id', String(36), ForeignKey('library_case_payloads.case_record_id', ondelete='CASCADE'), primary_key=True))
    shares = Table('library_share_requests', metadata,
        Column('id', String(36), primary_key=True),
        Column('case_record_id', String(36), ForeignKey('library_case_payloads.case_record_id', ondelete='CASCADE'), nullable=False),
        Column('owner_user_id', String(36), ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        Column('status', String(20), nullable=False),
        Column('created_at', DateTime(timezone=True), nullable=False),
        Column('reviewed_at', DateTime(timezone=True), nullable=True),
        Column('reviewed_by', String(36), ForeignKey('users.id'), nullable=True),
        UniqueConstraint('case_record_id', name='uq_native_case_share'))
    published = Table('library_published_cases', metadata,
        Column('case_id', String(160), primary_key=True),
        Column('origin_case_record_id', String(36), nullable=False),
        Column('case_json', Text, nullable=False),
        Column('tag_ids_json', Text, nullable=False),
        Column('published_at', DateTime(timezone=True), nullable=False))
    return SimpleNamespace(customers=customers, collections=collections, payloads=payloads,
                           memberships=memberships, shares=shares, published=published)
