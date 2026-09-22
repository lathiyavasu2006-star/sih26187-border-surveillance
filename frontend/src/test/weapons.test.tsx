import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { JobSummary, LiveAnalysisView } from '@/components/analysis/AnalysisViews'
import { WeaponCountBox } from '@/components/analysis/WeaponCountBox'
import { recordArmedTracks } from '@/stores/liveStore'
import type { AnalysisJob, WSDetection } from '@/types'

function job(overrides: Partial<AnalysisJob> = {}): AnalysisJob {
  return {
    job_id: 'job-1', evidence_id: null, camera_id: null, standalone: true, status: 'complete', percent: 100,
    frames_read: 948, frames_total: 948, persons: 4, vehicles: 0, animals: 0, weapons: 2, alerts: 2, video_seconds: 35,
    created_at: '', finished_at: null, error: null,
    summary: {
      persons_found: 4, vehicles_found: 0, animals_found: 0,
      weapons_found: { firearm: 2, knife: 1 }, weapon_sightings: { firearm: 41, knife: 6 },
      alerts_detected: 2, alerts_created: 0, evidence_saved: 0, high_risk_moments: [],
      duration_seconds: 35, frames_read: 948, frames_processed: 948, fps: 26.8, resolution: [1920, 1020], zones_used: 0,
    },
    ...overrides,
  } as AnalysisJob
}

function detection(trackId: number, weapon: string | null): WSDetection {
  return { track_id: trackId, object_class: 'person', weapon_class: weapon } as WSDetection
}

describe('WeaponCountBox', () => {
  it('lists every weapon type with its count and how often it was seen', () => {
    render(<WeaponCountBox counts={{ firearm: 2, knife: 1 }} sightings={{ firearm: 41, knife: 6 }} />)
    expect(screen.getByTestId('weapon-count-box')).toHaveAttribute('data-total', '3')
    expect(screen.getByTestId('weapon-firearm')).toHaveTextContent('2')
    expect(screen.getByTestId('weapon-firearm')).toHaveTextContent('seen in 41 frames')
    expect(screen.getByTestId('weapon-knife')).toHaveTextContent('1')
    expect(screen.getByText(/Counted per armed person, not per frame/)).toBeInTheDocument()
  })

  it('shows both types at zero and says nothing was confirmed', () => {
    render(<WeaponCountBox counts={{}} />)
    expect(screen.getByTestId('weapon-total')).toHaveTextContent('0')
    expect(screen.getByTestId('weapon-firearm')).toHaveTextContent('0')
    expect(screen.getByTestId('weapon-knife')).toHaveTextContent('0')
    expect(screen.getByText(/No weapon confirmed/)).toBeInTheDocument()
    // Operators must know what the model cannot see.
    expect(screen.getByText(/Rifles, sticks and rods are not trained yet/)).toBeInTheDocument()
  })

  it('can hide the coverage note', () => {
    render(<WeaponCountBox counts={{}} showNote={false} />)
    expect(screen.queryByText(/not trained yet/)).not.toBeInTheDocument()
  })
})

describe('weapon counts in video analysis', () => {
  it('shows the weapon box in the analysis summary', () => {
    render(<JobSummary job={job()} />)
    expect(screen.getByTestId('weapon-count-box')).toHaveAttribute('data-total', '3')
  })

  it('shows the running weapon count while the video is analysed', () => {
    render(<LiveAnalysisView job={job({ status: 'running', percent: 40 })} frame={null} live />)
    expect(screen.getByTestId('live-weapons')).toHaveTextContent('WEAPONS 2')
  })
})

describe('recordArmedTracks', () => {
  it('counts each armed person once per weapon type, across updates', () => {
    let armed = recordArmedTracks({}, [detection(1, 'firearm'), detection(2, null), detection(3, 'knife')])
    armed = recordArmedTracks(armed, [detection(1, 'firearm'), detection(4, 'firearm')])
    expect(armed).toEqual({ firearm: [1, 4], knife: [3] })
  })

  it('returns the same object when nobody is armed (no needless re-render)', () => {
    const previous = { firearm: [1] }
    expect(recordArmedTracks(previous, [detection(2, null)])).toBe(previous)
  })
})
