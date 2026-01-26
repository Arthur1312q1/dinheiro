#!/usr/bin/env python3
import os
import glob

def clean_python_files():
    """Remove as tags [file name]: de todos os arquivos Python"""
    python_files = glob.glob("*.py") + glob.glob("src/*.py")
    
    for file_path in python_files:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Remover tags [file name]:, [file content begin], [file content end]
            lines = content.split('\n')
            cleaned_lines = []
            
            for line in lines:
                if line.strip().startswith('[file name]:'):
                    continue
                if line.strip().startswith('[file content begin]'):
                    continue
                if line.strip().startswith('[file content end]'):
                    continue
                cleaned_lines.append(line)
            
            # Se houve alteração, salvar
            if len(cleaned_lines) != len(lines):
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(cleaned_lines))
                print(f"✅ Limpo: {file_path}")
            else:
                print(f"✓ Já limpo: {file_path}")
                
        except Exception as e:
            print(f"❌ Erro em {file_path}: {e}")

if __name__ == "__main__":
    print("🧹 Limpando tags dos arquivos Python...")
    clean_python_files()
    print("✅ Concluído!")
