// Plantilla base del frontend (todo el grupo).
// Reemplazar por: feed en vivo (WebSocket /ws/notifications?user_id=),
// búsqueda (GET /api/search) y preferencias (PUT /api/users/{id}/preferences).
export default function App() {
  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: 24 }}>
      <h1>Agregador de Noticias</h1>
      <p>Plantilla base. El frontend habla solo con el gateway por /api y /ws.</p>
    </main>
  );
}
