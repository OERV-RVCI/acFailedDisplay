
from collections import defaultdict, namedtuple
import datetime
from enum import Enum
import json
from pathlib import Path
from pprint import pprint
from collections import defaultdict
from datetime import datetime

import requests
import yaml

import urllib3
urllib3.disable_warnings()


class EbsQuery:
    def __init__(self,*k, **kw):
        size = k[0] if k else 1
        self._query = {
        "index": '',
        "query": {
            "size": size,
            "_source": [],
            "query": {},
            # "sort": []
        }
    }

    def index(self, index):
        self._query['index'] = index

    def sort(self, **kw):
        _query: dict = self._query['query']
        sort: list = _query.setdefault('sort', [])
        for k,v in kw.items():
            # if 'desc' in v:
            sort.extend([{k:{'order': v}}])
        return self

    def projects(self, *source, **kw):
        self.index('projects')
        if source:
            self._query['query']['_source'] = source
        return self

    def builds(self, *source, **kw):
        self.index('builds')
        if source:
            self._query['query']['_source'] = source
        return self

    def jobs(self, *source, **kw):
        self.index('jobs')
        if source:
            (_source := self._query['query']['_source']).extend(source)
        return self

    def rpms(self, *source, **kw):
        self.index('rpms')
        if source:
            (_source := self._query['query']['_source']).extend(source)
        return self

    def must(self, **kw):
        query: dict = self._query['query']['query']
        bool: dict = query.setdefault('bool', {})
        must: list = bool.setdefault('must', [])
        must.extend([{"term": {k: str(v)}} for k,v in kw.items()])
        return self

    def must_not(self, **kw):
        query: dict = self._query['query']['query']
        bool: dict = query.setdefault('bool', {})
        must_not: list = bool.setdefault('must_not', [])
        must_not.extend([{"term": {k: str(v)}} for k,v in kw.items()])
        return self

    def match(self, **kw):
        query: dict = self._query['query']['query']
        match: dict = query.setdefault('match', {})
        match.update(kw)
        return self

    def group_by(self, **kw):
        _source: list = self._query['query']['_source']
        aggs, top = {}, None
        for source in reversed(kw):
            size = kw[source]
            if not source in _source:
                _source.append(source)
            if not top:
                top = { "doc_top_1": { "top_hits": { "size": 1, "_source": _source, "sort":[ {"submit_time": { "order": "desc" }} ] }}}
                aggs = { f'group_by_{source}': { 'terms':{ 'size': size, 'field': f'{source}' }, 'aggs':top } }
            else:
                aggs = {f'group_by_{source}': {'terms':{'size': size,'field':f'{source}'}, 'aggs':aggs }}

        self._query['query'].setdefault('aggs', aggs)
        return self



ENDPOINT = "https://eulermaker.compass-ci.openeuler.openatom.cn/api/data-api/search"

class BUILD_STATUS(Enum):
    BUILDING = 200
    SUCCESS = 201
    FAILED = 202
    ABORTED = 203
    BLOCKED = 204
    EXCLUDED = 205

    def __str__(self):
        return self.name



def check_projects():
    # 检查存在的pr任务
    e = EbsQuery(4000).projects().must(to_delete='false').must_not(owner="admin").sort(create_time='desc')
    resp = requests.post(json=e._query, url=ENDPOINT)
    search = resp.json()

    pr_list = []
    for hit in search['hits']['hits']:
        os_project, item = hit['_id'], hit['_source']
        # description: dict = item['build_packages']
        owner: str = item['owner']
        # print(os_project, item)
        if 'ci_soe' in (project_type := item.get('project_type', '')):
            build_targets: list = item['build_targets']
            for target in build_targets:
                if (arch := target.get('architecture', None)) is not None and 'riscv64' in arch:
                    package_repos: list = item['package_repos']
                    package_overrides: dict = item['package_overrides']
                    description = item['description']
                    my_specs: list = item['my_specs']
                    # print(os_project, description, my_specs, package_overrides)
                    pr_list.append((os_project, package_overrides, description, my_specs))

    result_list = []

    for os_project, package_overrides, description, my_specs in pr_list:
        e = EbsQuery(100)\
            .builds()\
            .must(os_project=os_project)

        resp = requests.post(json=e._query, url=ENDPOINT)
        search = resp.json()

        

        for hit in search['hits']['hits']:
            build_id = hit['_id']
            item = hit['_source']

            status = BUILD_STATUS._value2member_map_.get(item['status'])
            ebs_url = f'https://eulermaker.compass-ci.openeuler.openatom.cn/project/overview?osProject={os_project}'
            pr_url = description
            if f'{status}' == 'FAILED':
                result_list.append(f"{item['create_time']} , {item['packages']}, {status}, {pr_url}, {ebs_url}")
                #print(result_list)
                #print(item['packages'], status, pr_url, ebs_url)
    return result_list

def generate_report_with_latest_timestamp(data_list):
    """
    data_list格式：每条是字符串 "时间戳, 包名, 状态, PR链接, 构建链接"
    去重规则：相同包名+PR链接只保留时间戳最新的记录
    """
    
    package_pr_map = {}  # key: (package, pr_link), value: 完整记录信息
    
    for entry in data_list:
        parts = [x.strip() for x in entry.split(",")]
        if len(parts) < 5:
            continue
            
        timestamp_str, package, status, pr_link, build_link = parts[:5]
        
        try:
            timestamp = datetime.fromisoformat(timestamp_str.replace('+0800', '+08:00'))
        except ValueError:
            continue
            
        key = (package, pr_link)
        record = {
            "timestamp": timestamp,
            "timestamp_str": timestamp_str,
            "package": package,
            "status": status.upper(),
            "pr_link": pr_link,
            "build_link": build_link
        }
        
        # 加入调试打印，标明当前记录是新、更新还是跳过
        if key not in package_pr_map:
            print(f"新记录: {package} PR#{pr_link.rstrip('/').split('/')[-1]} 时间: {timestamp}")
            package_pr_map[key] = record
        elif timestamp > package_pr_map[key]["timestamp"]:
            old_time = package_pr_map[key]["timestamp"]
            print(f"更新记录: {package} PR#{pr_link.rstrip('/').split('/')[-1]} 从 {old_time} 更新到 {timestamp}")
            package_pr_map[key] = record
        else:
            print(f"跳过旧记录: {package} PR#{pr_link.rstrip('/').split('/')[-1]} 时间: {timestamp}")
    
    unique_records = list(package_pr_map.values())
    unique_records.sort(key=lambda x: x["timestamp"], reverse=True)
    
    status_counter = defaultdict(int)
    for rec in unique_records:
        status_counter[rec["status"]] += 1
    
    status_emoji = {
        "FAILED": "❌",
        "SUCCESS": "✅",
        "RUNNING": "🔄",
        "PENDING": "⏳",
        "CANCELLED": "⛔"
    }
    
    report_lines = []
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    report_lines.append(f"# 构建状态报告\n")
    report_lines.append(f"**生成时间**: {now_str}")
    report_lines.append(f"**数据条数**: 原始 {len(data_list)} 条，去重后 {len(unique_records)} 条\n")
    
    report_lines.append("## 📊 构建状态汇总\n")
    if status_counter:
        for st in sorted(status_counter.keys()):
            count = status_counter[st]
            emoji = status_emoji.get(st, "❓")
            report_lines.append(f"- {emoji} **{st}**: {count} 个包")
        report_lines.append("")
    else:
        report_lines.append("无构建记录\n")
    
    report_lines.append("## 📋 构建详情\n")
    report_lines.append("*按最新时间排序，相同包名+PR只显示最新记录*\n")
    report_lines.append("| 最新时间 | 包名 | 状态 | PR链接 | 构建链接 |")
    report_lines.append("|------|------|----------|--------|----------|")
    
    for rec in unique_records:
        emoji = status_emoji.get(rec["status"], "❓")
        status_disp = f"{emoji} {rec['status']}"
        time_display = rec["timestamp"].strftime('%m-%d %H:%M')
        pr_num = rec["pr_link"].rstrip('/').split('/')[-1]
        pr_link_md = f"[PR #{pr_num}]({rec['pr_link']})"
        build_link_md = f"[构建详情]({rec['build_link']})"
        report_lines.append(
            f"| {time_display} | {rec['package']} | {status_disp} | {pr_link_md} | {build_link_md} |"
        )
    
    return "\n".join(report_lines)






data_list = check_projects()
#print("##########################")
#print(data_list)
# 生成报告
report_markdown = generate_report_with_latest_timestamp(data_list)

# 打印输出
#print(report_markdown)
with open("README.md", "w", encoding="utf-8") as f:
    f.write(report_markdown)