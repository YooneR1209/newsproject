// Rutas de la app. Cada página la implementa su responsable (frontend/CLAUDE.md).
import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout.jsx";
import Feed from "./pages/Feed.jsx";
import Buscar from "./pages/Buscar.jsx";
import Fuentes from "./pages/Fuentes.jsx";
import Preferencias from "./pages/Preferencias.jsx";
import { getUserId } from "./user.js";

// Sin usuario registrado, todo redirige a /preferencias (ahí está el registro).
function RequiereUsuario({ children }) {
  if (!getUserId()) return <Navigate to="/preferencias" replace />;
  return children;
}

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/feed" replace />} />
        <Route path="feed" element={<RequiereUsuario><Feed /></RequiereUsuario>} />
        <Route path="buscar" element={<RequiereUsuario><Buscar /></RequiereUsuario>} />
        <Route path="fuentes" element={<RequiereUsuario><Fuentes /></RequiereUsuario>} />
        <Route path="preferencias" element={<Preferencias />} />
        <Route path="*" element={<Navigate to="/feed" replace />} />
      </Route>
    </Routes>
  );
}
