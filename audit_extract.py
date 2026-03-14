import os
import ast

def analyze_codebase(cwd):
    commands = []
    buttons = []
    
    for root, dirs, files in os.walk(cwd):
        if 'venv' in root or '.git' in root:
            continue
        for file in files:
            if file.endswith('.py'):
                filepath = os.path.join(root, file)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        source = f.read()
                    tree = ast.parse(source)
                    
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Call):
                            if isinstance(node.func, ast.Name) and node.func.id == 'CommandHandler':
                                if len(node.args) >= 1 and isinstance(node.args[0], ast.Constant):
                                    commands.append((file, getattr(node.args[0], 'value', str(node.args[0]))))
                            if isinstance(node.func, ast.Name) and node.func.id == 'InlineKeyboardButton':
                                if len(node.args) >= 1 and isinstance(node.args[0], ast.Constant):
                                    buttons.append((file, getattr(node.args[0], 'value', str(node.args[0]))))
                except Exception as e:
                    pass
    
    with open('audit_output.txt', 'w', encoding='utf-8') as f:
        f.write(f"Total Commands: {len(commands)}\n")
        cmds = set([c[1] for c in commands])
        for c in sorted(list(cmds)):
            f.write(f"- /{c}\n")
        
        f.write("\n\n")
        f.write(f"Total Buttons: {len(buttons)}\n")
        btns = set([b[1] for b in buttons])
        for b in sorted(list(btns)):
            f.write(f"- {b}\n")

if __name__ == '__main__':
    analyze_codebase(r'c:\Users\User\Downloads\antiriya\riya-stories-bot')
