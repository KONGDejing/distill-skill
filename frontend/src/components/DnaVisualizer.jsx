export default function DnaVisualizer({ dna }) {
  if (!dna) return null

  const sections = [
    {
      title: '价值观定位',
      color: 'blue',
      data: dna.value_positioning,
      render: (d) => (
        <div className="space-y-3">
          <DnaRow label="核心理念" value={d?.core_values?.join('、')} />
          <DnaRow label="人设" value={d?.persona} />
          <DnaRow label="人设特征" value={d?.persona_traits?.join('、')} />
          <DnaRow label="目标受众" value={d?.target_audience} />
          <DnaRow label="情绪调动" value={d?.emotional_appeal} />
          <DnaRow label="差异化" value={d?.differentiation} />
        </div>
      ),
    },
    {
      title: '爆款技巧',
      color: 'orange',
      data: dna.viral_techniques,
      render: (d) => (
        <div className="space-y-3">
          <DnaRow label="叙事结构" value={d?.narrative_structure} />
          <DnaRow label="节奏控制" value={d?.rhythm_control} />
          <DnaRow label="互动引导" value={d?.interaction_guide} />
          <DnaRow label="高潮设计" value={d?.climax_design} />
          {d?.hook_patterns && (
            <div>
              <span className="text-xs text-gray-500">钩子模式</span>
              <div className="mt-1 space-y-1">
                {d.hook_patterns.map((h, i) => (
                  <div key={i} className="bg-gray-800 rounded p-2 text-xs">
                    <span className="text-orange-400 font-medium">{h.type}</span>
                    <span className="text-gray-400"> — {h.description}</span>
                    {h.example && <p className="text-gray-500 mt-0.5">例: "{h.example}"</p>}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      ),
    },
    {
      title: '选题偏好',
      color: 'green',
      data: dna.content_preferences,
      render: (d) => (
        <div className="space-y-3">
          <DnaRow label="高频话题" value={d?.top_topics?.join('、')} />
          <DnaRow label="切入角度" value={d?.content_angles?.join('、')} />
          <DnaRow label="禁忌话题" value={d?.taboo_topics?.join('、')} />
          <DnaRow label="内容形式" value={d?.format_preference} />
          <DnaRow label="最佳时长" value={d?.optimal_duration} />
        </div>
      ),
    },
    {
      title: '话术风格',
      color: 'purple',
      data: dna.language_style,
      render: (d) => (
        <div className="space-y-3">
          <DnaRow label="语气" value={d?.tone} />
          <DnaRow label="口头禅" value={d?.catchphrases?.join('、')} />
          <DnaRow label="句式特点" value={d?.sentence_pattern} />
          <DnaRow label="开头风格" value={d?.opening_style} />
          <DnaRow label="结尾风格" value={d?.closing_style} />
          <DnaRow label="语速" value={d?.pace} />
        </div>
      ),
    },
    {
      title: '发布策略',
      color: 'pink',
      data: dna.content_calendar,
      render: (d) => (
        <div className="space-y-3">
          <DnaRow label="发布频率" value={d?.estimated_frequency} />
          <DnaRow label="最佳类型" value={d?.best_content_types?.join('、')} />
          <DnaRow label="建议时段" value={d?.suggested_posting_times?.join('、')} />
        </div>
      ),
    },
  ]

  const borderColors = {
    blue: 'border-blue-500/30',
    orange: 'border-orange-500/30',
    green: 'border-green-500/30',
    purple: 'border-purple-500/30',
    pink: 'border-pink-500/30',
  }
  const titleColors = {
    blue: 'text-blue-400',
    orange: 'text-orange-400',
    green: 'text-green-400',
    purple: 'text-purple-400',
    pink: 'text-pink-400',
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      {sections.map(({ title, color, data, render }) => (
        data && (
          <div key={title} className={`bg-gray-900 rounded-xl p-5 border ${borderColors[color]}`}>
            <h4 className={`font-semibold mb-3 ${titleColors[color]}`}>{title}</h4>
            {render(data)}
          </div>
        )
      ))}
    </div>
  )
}

function DnaRow({ label, value }) {
  if (!value) return null
  return (
    <div>
      <span className="text-xs text-gray-500">{label}</span>
      <p className="text-sm mt-0.5">{value}</p>
    </div>
  )
}
