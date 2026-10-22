"""
Verify that the current database schema matches the SQLAlchemy models.
"""
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import inspect
from backend.app.core.database import engine, Base

# Import all models
from backend.app.models.repository import Repository
from backend.app.models.repository_file import RepositoryFile
from backend.app.models.code_symbol import CodeSymbol
from backend.app.models.code_import import CodeImport
from backend.app.models.code_chunk import CodeChunk
from backend.app.models.code_call import CodeCall


def verify_schema():
    """Verify existing database schema matches models."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    
    # Expected tables from models
    expected_tables = {table.name for table in Base.metadata.sorted_tables}
    
    print("=" * 70)
    print("DATABASE SCHEMA VERIFICATION")
    print("=" * 70)
    print(f"\nDatabase: {engine.url.database}")
    print(f"Expected tables ({len(expected_tables)}): {sorted(expected_tables)}")
    print(f"Existing tables ({len(existing_tables)}): {sorted(existing_tables)}")
    
    missing_tables = expected_tables - existing_tables
    extra_tables = existing_tables - expected_tables
    
    if missing_tables:
        print(f"\n⚠️  MISSING TABLES: {sorted(missing_tables)}")
        return False
    
    if extra_tables:
        print(f"\n⚠️  EXTRA TABLES (not in models): {sorted(extra_tables)}")
    
    print("\n✅ All expected tables exist in the database")
    
    # Verify table structures
    print("\n" + "=" * 70)
    print("TABLE STRUCTURE VERIFICATION")
    print("=" * 70)
    
    all_match = True
    for table_name in sorted(expected_tables):
        table = Base.metadata.tables[table_name]
        db_columns = {col['name']: col for col in inspector.get_columns(table_name)}
        model_columns = {col.name: col for col in table.columns}
        
        print(f"\n📋 Table: {table_name}")
        print(f"   Model columns: {sorted(model_columns.keys())}")
        print(f"   DB columns: {sorted(db_columns.keys())}")
        
        missing_cols = set(model_columns.keys()) - set(db_columns.keys())
        extra_cols = set(db_columns.keys()) - set(model_columns.keys())
        
        if missing_cols:
            print(f"   ⚠️  Missing columns in DB: {sorted(missing_cols)}")
            all_match = False
        
        if extra_cols:
            print(f"   ⚠️  Extra columns in DB: {sorted(extra_cols)}")
            all_match = False
        
        if not missing_cols and not extra_cols:
            print(f"   ✅ Column names match")
        
        # Check foreign keys
        db_fks = inspector.get_foreign_keys(table_name)
        print(f"   Foreign keys: {len(db_fks)}")
        
        # Check indexes
        db_indexes = inspector.get_indexes(table_name)
        print(f"   Indexes: {len(db_indexes)}")
    
    print("\n" + "=" * 70)
    if all_match:
        print("✅ DATABASE SCHEMA MATCHES MODELS")
        print("You can safely stamp the database with: alembic stamp head")
    else:
        print("⚠️  SCHEMA MISMATCH DETECTED")
        print("Review differences before running migrations")
    print("=" * 70)
    
    return all_match


if __name__ == "__main__":
    try:
        matches = verify_schema()
        sys.exit(0 if matches else 1)
    except Exception as e:
        print(f"\n❌ Error verifying schema: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(2)
