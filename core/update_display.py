import re

with open('c:/Projects/PyProjects/stock-analysis/core/TradingAnalysis_ELITE.py', 'r', encoding='utf-8') as f:
    content = f.read()

with open('c:/Projects/PyProjects/stock-analysis/core/rich_display.py', 'r', encoding='utf-8') as f:
    rich_content = f.read()

start_idx = content.find('def display_analysis(result: \'AnalysisResult\', detailed: bool = True):')
end_idx = content.find('def display_position_analysis(result: \'AnalysisResult\', shares: int, avg_cost: float,')

if start_idx != -1 and end_idx != -1:
    new_content = content[:start_idx] + rich_content + '\n\n' + content[end_idx:]
    with open('c:/Projects/PyProjects/stock-analysis/core/TradingAnalysis_ELITE.py', 'w', encoding='utf-8') as f:
        f.write(new_content)
    print('Successfully replaced display_analysis')
else:
    print(f'Could not find display_analysis: start={start_idx}, end={end_idx}')
