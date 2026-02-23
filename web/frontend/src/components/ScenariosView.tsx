interface ScenariosViewProps {
  /** Callback вызывается при запуске сценария — переключает вид на монитор. */
  onLaunch: () => void
}

/**
 * Заглушка представления «Сценарии».
 * Будет реализована в следующих итерациях.
 */
export function ScenariosView(_props: ScenariosViewProps) {
  return (
    <div className="scenarios-view">
      <div className="text-muted">Сценарии — скоро</div>
    </div>
  )
}
