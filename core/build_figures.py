from pathlib import Path
import json, hashlib, shutil
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np

HERE=Path(__file__).resolve().parent
OUT=HERE/'images'; OUT.mkdir(exist_ok=True)
s=json.loads((HERE/'chart-data.json').read_text(encoding='utf-8'))
font_manager.fontManager.addfont('C:/Windows/Fonts/msyh.ttc')
plt.rcParams.update({'font.family':['Microsoft YaHei','DejaVu Sans'],'font.size':10,
 'axes.spines.top':False,'axes.spines.right':False,'axes.spines.left':False,
 'axes.unicode_minus':False,'pdf.fonttype':42,'text.color':'#172D40'})
colors=['#4178A8','#DEAB36','#2B998C','#9472B3']
modules=['图片标注','音乐分类','图片综合','视频综合']
e=sorted(s['experiments'],key=lambda x:(-x['total'],x['public_label']))
ys=np.arange(25)
fig,ax=plt.subplots(figsize=(10.8,12.5))
left=np.zeros(25)
for j in range(4):
 v=np.array([x['modules'][str(j+1)] for x in e]);ax.barh(ys,v,left=left,color=colors[j],height=.68,label=modules[j]);left+=v
labels=[]
group_marks={'agents':'a','traditional':'b','players':'c'}
for x in e:
 label=x['public_label'].replace(' / ','\n').replace('（思考档位不可选）','（固定）').replace('stdlib Python fixed runner','固定问答脚本')
 labels.append('$^{['+group_marks[x['group']]+']}$ '+label)
ax.set_yticks(ys,labels,fontsize=9);ax.tick_params(axis='y',length=0,pad=9)
for y,x in zip(ys,e):ax.text(x['total']+1,y,f"{x['total']:g}",va='center',fontsize=9)
ax.set_xlim(0,104);ax.invert_yaxis();ax.set_xticks(range(0,101,20));ax.set_xlabel('总分 / 100（各色段为模块实际得分）')
ax.xaxis.grid(True,color='#E3E8EC',linewidth=.6);ax.set_axisbelow(True)
ax.legend(ncol=4,loc='lower left',bbox_to_anchor=(-.3,1.005),frameon=False,fontsize=10)
fig.subplots_adjust(left=.34,right=.94,top=.95,bottom=.06)
fig.savefig(OUT/'scores.pdf');fig.savefig(OUT/'scores.png',dpi=170);plt.close(fig)

fig,axes=plt.subplots(1,2,figsize=(10,3.4),gridspec_kw={'width_ratios':[1,2.3]})
for i,(g,label,col) in enumerate([('agents','智能体配置','#4178A8'),('players','活跃玩家','#9472B3')]):
 a=np.array([x['total'] for x in e if x['group']==g]);mean=a.mean()
 axes[0].errorbar(i,mean,yerr=[[mean-a.min()],[a.max()-mean]],fmt='o',color=col,capsize=6,markersize=8)
 axes[0].text(i+.10,mean,f'{mean:.2f}',fontsize=10,va='center')
 axes[0].text(i,a.max()+3,f'{a.max():g}',ha='center',fontsize=9)
 axes[0].text(i,a.min()-7,f'{a.min():g}',ha='center',fontsize=9)
axes[0].set(xlim=(-.5,1.7),ylim=(0,109),xticks=[0,1],xticklabels=['智能体\n(n=18)','玩家\n(n=5)'],ylabel='总分 / 100')
axes[0].set_title('均值与最小—最大值',fontsize=11)
x=np.arange(4)
for i,(g,label,col) in enumerate([('agents','智能体','#4178A8'),('players','玩家','#9472B3')]):
 a=np.array([[z['modules'][str(j+1)] for j in range(4)] for z in e if z['group']==g]);means=a.mean(axis=0)/[34,6,30,30]*100
 axes[1].bar(x+(i-.5)*.36,means,width=.34,color=col,label=label)
 for xx,v in zip(x+(i-.5)*.36,means):axes[1].text(xx,v+2,f'{v:.1f}',ha='center',fontsize=9)
axes[1].set(xticks=x,xticklabels=modules,ylim=(0,110),ylabel='模块平均得分率 / %')
axes[1].legend(frameon=False,ncol=2,loc='upper right');axes[1].set_title('按模块满分归一化',fontsize=11)
for ax in axes:ax.yaxis.grid(True,color='#E3E8EC');ax.set_axisbelow(True)
fig.tight_layout();fig.savefig(OUT/'groups.pdf');fig.savefig(OUT/'groups.png',dpi=170);plt.close(fig)
print('Rebuilt charts from bundled aggregate data.')
