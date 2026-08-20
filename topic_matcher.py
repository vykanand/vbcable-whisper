import json
import re
from collections import defaultdict

class TopicMatcher:
    def __init__(self, topics_file='topics.json'):
        with open(topics_file, 'r', encoding='utf-8') as f:
            self.topics_data = json.load(f)
        self.index = self._build_index()
    
    def _build_index(self):
        index = defaultdict(list)
        for category in self.topics_data.get('categories', []):
            for topic in category.get('topics', []):
                concepts = topic.get('concepts', [])
                resources = topic.get('resources', [])
                title = topic.get('title', '')
                
                search_text = ' '.join(concepts).lower() + ' ' + title.lower()
                
                for res in resources:
                    url = res.get('url', '')
                    res_title = res.get('title', '')
                    index_key = url if url and not url.startswith('/') else res_title
                    
                for keyword in search_text.split():
                    keyword = keyword.strip()
                    if keyword and len(keyword) > 2:
                        for res in resources:
                            url = res.get('url', '')
                            if url:
                                index[keyword].append({
                                    'url': url,
                                    'title': res.get('title', ''),
                                    'type': res.get('type', 'unknown'),
                                    'topic_title': title,
                                    'category': category.get('name', category.get('id', ''))
                                })
        return index
    
    def match(self, text, threshold=0.3):
        text = text.lower()
        words = set(text.split())
        matches = []
        
        for word in words:
            if word in self.index:
                for item in self.index[word]:
                    matches.append(item)
        
        unique = {}
        for m in matches:
            key = m['url']
            if key not in unique:
                unique[key] = m
        
        return list(unique.values())[:5]

if __name__ == '__main__':
    matcher = TopicMatcher()
    test_text = "How would you design Architecture for microservices"
    results = matcher.match(test_text)
    for r in results:
        print(f"{r['title']} -> {r['url']}")