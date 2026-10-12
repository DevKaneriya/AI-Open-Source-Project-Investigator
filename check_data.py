#!/usr/bin/env python3
"""Diagnostic script to check database state"""

from backend.app.core.database import SessionLocal
from backend.app.models.repository import Repository
from backend.app.models.code_symbol import CodeSymbol
from backend.app.models.code_chunk import CodeChunk
from backend.app.models.repository_file import RepositoryFile

def main():
    db = SessionLocal()
    
    try:
        # Check repositories
        repos = db.query(Repository).all()
        print(f"\n{'='*70}")
        print(f"REPOSITORIES: {len(repos)}")
        print(f"{'='*70}")
        
        for r in repos:
            print(f"\nRepository ID: {r.id}")
            print(f"  Name: {r.name}")
            print(f"  URL: {r.url}")
            print(f"  Status: {r.status}")
            print(f"  Local Path: {r.local_path}")
            
            # Count indexed files for this repo
            files = db.query(RepositoryFile).filter_by(repository_id=r.id).all()
            print(f"  Indexed Files: {len(files)}")
            
            # Count symbols for this repo
            if files:
                file_ids = [f.id for f in files]
                symbols = db.query(CodeSymbol).filter(CodeSymbol.file_id.in_(file_ids)).count()
                print(f"  Indexed Symbols: {symbols}")
                
                # Sample first 5 symbols
                sample_symbols = db.query(CodeSymbol).filter(CodeSymbol.file_id.in_(file_ids)).limit(5).all()
                if sample_symbols:
                    print(f"\n  Sample Symbols:")
                    for s in sample_symbols:
                        print(f"    - {s.name} ({s.symbol_type}) in {files[0].path if files else 'unknown'}")
            
            # Count chunks for this repo
            chunks = db.query(CodeChunk).filter_by(repository_id=r.id).count()
            print(f"  Indexed Chunks: {chunks}")
        
        # Global stats
        print(f"\n{'='*70}")
        print(f"GLOBAL STATISTICS")
        print(f"{'='*70}")
        print(f"Total Repositories: {len(repos)}")
        print(f"Total Files: {db.query(RepositoryFile).count()}")
        print(f"Total Symbols: {db.query(CodeSymbol).count()}")
        print(f"Total Chunks: {db.query(CodeChunk).count()}")
        print(f"{'='*70}\n")
        
    finally:
        db.close()

if __name__ == "__main__":
    main()
