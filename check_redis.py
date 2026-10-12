#!/usr/bin/env python3
"""Check if Redis/Celery are in the repository"""

from backend.app.core.database import SessionLocal
from backend.app.models.code_symbol import CodeSymbol
from backend.app.models.code_import import CodeImport
from backend.app.models.repository_file import RepositoryFile
from sqlalchemy import or_

def main():
    db = SessionLocal()
    repository_id = 1
    
    try:
        # Check symbols containing 'redis' or 'celery'
        print("\n[1] Checking Symbols...")
        symbols = db.query(CodeSymbol).join(RepositoryFile).filter(
            RepositoryFile.repository_id == repository_id,
            or_(
                CodeSymbol.name.ilike('%redis%'),
                CodeSymbol.name.ilike('%celery%'),
                CodeSymbol.qualified_name.ilike('%redis%'),
                CodeSymbol.qualified_name.ilike('%celery%')
            )
        ).all()
        
        print(f"Found {len(symbols)} symbols with 'redis' or 'celery'")
        for s in symbols[:10]:
            print(f"  - {s.name} ({s.symbol_type})")
        
        # Check imports
        print("\n[2] Checking Imports...")
        imports = db.query(CodeImport).join(RepositoryFile).filter(
            RepositoryFile.repository_id == repository_id,
            or_(
                CodeImport.imported_module.ilike('%redis%'),
                CodeImport.imported_module.ilike('%celery%')
            )
        ).all()
        
        print(f"Found {len(imports)} imports with 'redis' or 'celery'")
        for imp in imports[:10]:
            file = db.query(RepositoryFile).filter_by(id=imp.file_id).first()
            print(f"  - {imp.imported_module} in {file.path if file else 'unknown'}")
        
        # Sample all imports to see what's there
        print("\n[3] Sample of ALL imports...")
        all_imports = db.query(CodeImport).join(RepositoryFile).filter(
            RepositoryFile.repository_id == repository_id
        ).limit(20).all()
        
        for imp in all_imports:
            file = db.query(RepositoryFile).filter_by(id=imp.file_id).first()
            print(f"  - {imp.imported_module} in {file.path if file else 'unknown'}")
        
    finally:
        db.close()

if __name__ == "__main__":
    main()
