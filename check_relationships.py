#!/usr/bin/env python3
"""Check if code call relationships are indexed"""

from backend.app.core.database import SessionLocal
from backend.app.models.code_call import CodeCall
from backend.app.models.code_symbol import CodeSymbol
from backend.app.models.repository_file import RepositoryFile
from sqlalchemy import select

def main():
    db = SessionLocal()
    repository_id = 1
    
    try:
        print(f"\n{'='*70}")
        print("CHECKING CODE CALL RELATIONSHIPS")
        print(f"{'='*70}\n")
        
        # Count total calls
        total_calls = db.query(CodeCall).count()
        print(f"Total CodeCall records: {total_calls}")
        
        if total_calls == 0:
            print("\n⚠️  NO CALL RELATIONSHIPS INDEXED!")
            print("   The relationship feature has NOT been run during indexing.")
            print("   This explains why it's disabled in retrieval.\n")
            return
        
        # Sample some calls
        print(f"\n[Sample Call Relationships]")
        
        statement = (
            select(CodeSymbol, CodeCall, RepositoryFile)
            .join(CodeCall, CodeCall.symbol_id == CodeSymbol.id)
            .join(RepositoryFile, RepositoryFile.id == CodeSymbol.file_id)
            .where(RepositoryFile.repository_id == repository_id)
            .limit(10)
        )
        
        results = db.execute(statement).all()
        
        if not results:
            print("  No calls found for this repository")
            return
        
        for symbol, call, file in results:
            print(f"\n  Caller: {symbol.name} ({symbol.symbol_type})")
            print(f"    File: {file.path}")
            print(f"    Calls: {call.called_name}")
            
            # Check if called_name looks malformed
            if not call.called_name or len(call.called_name) < 2:
                print(f"    ⚠️  MALFORMED: Too short")
            elif any(char in call.called_name for char in ['{', '}', '(', ')', '[', ']', ',', ';']):
                print(f"    ⚠️  MALFORMED: Contains invalid characters")
        
        # Check for malformed calls
        print(f"\n[Checking for Malformed Calls]")
        all_calls = db.query(CodeCall).join(
            CodeSymbol, CodeCall.symbol_id == CodeSymbol.id
        ).join(
            RepositoryFile, CodeSymbol.file_id == RepositoryFile.id
        ).filter(
            RepositoryFile.repository_id == repository_id
        ).all()
        
        malformed = [
            c for c in all_calls 
            if not c.called_name 
            or len(c.called_name) < 2
            or any(char in c.called_name for char in ['{', '}', '(', ')', '[', ']'])
        ]
        
        print(f"  Total calls: {len(all_calls)}")
        print(f"  Malformed calls: {len(malformed)}")
        
        if malformed:
            print(f"\n  Sample malformed calls:")
            for c in malformed[:5]:
                print(f"    - '{c.called_name}'")
        
        print(f"\n{'='*70}\n")
        
    finally:
        db.close()

if __name__ == "__main__":
    main()
