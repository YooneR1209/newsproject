// Barra superior común a todas las páginas.
import { NavLink, Outlet } from "react-router-dom";
import { useNotificaciones } from "../context/NotificacionesContext.jsx";

const enlace = ({ isActive }) => (isActive ? "nav-link active" : "nav-link");

export default function Layout() {
  const { unread } = useNotificaciones();

  return (
    <>
      <header className="barra">
        <span className="marca">Agregador de Noticias</span>
        <nav className="menu">
          <NavLink to="/feed" className={enlace}>
            Feed
            {unread > 0 && <span className="badge">{unread}</span>}
          </NavLink>
          <NavLink to="/buscar" className={enlace}>Buscar</NavLink>
          <NavLink to="/fuentes" className={enlace}>Fuentes</NavLink>
          <NavLink to="/preferencias" className={enlace}>Preferencias</NavLink>
        </nav>
      </header>
      <main className="contenido">
        <Outlet />
      </main>
    </>
  );
}
